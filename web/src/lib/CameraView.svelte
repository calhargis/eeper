<script lang="ts">
  // One camera's live view: a recv-only WebRTC session (video + optional Opus audio),
  // the LIVE badge, and — when the source carries audio — a listen-in toggle and a
  // volume slider. Owns its own peer connection: mounting connects, unmounting tears
  // down, so switching inputs on the Live page never leaks a stream. The <video>
  // exposes the same data-* the M1.2/M2.1 Playwright contract reads (frames, audio
  // track/packets, jitter latency).
  import { onDestroy, onMount } from 'svelte';
  import type { Camera } from '$lib/api';
  import {
    connectCamera,
    inboundAudioStats,
    inboundVideoStats,
    type LiveSession,
  } from '$lib/webrtc';

  let { camera }: { camera: Camera } = $props();

  type Status = 'connecting' | 'live' | 'error';
  let videoEl = $state<HTMLVideoElement | undefined>();
  let status = $state<Status>('connecting');
  let framesDecoded = $state(0);
  let jitterBufferMs = $state<number | null>(null);
  let audioTrack = $state(false);
  let audioPackets = $state(0);
  let errorMsg = $state('');

  // Audio is muted by default (a muted <video> autoplays; unmuting is the user's
  // "listen in" gesture). Volume is remembered across a mute/unmute.
  let listening = $state(false);
  let volume = $state(80); // 0..100

  let session: LiveSession | null = null;
  let statsTimer: ReturnType<typeof setInterval> | null = null;
  let destroyed = false;

  // Keep the element's playout controls in sync. Mute is local-only — RTP still flows,
  // so the audio-packet contract holds whether or not the user is listening.
  $effect(() => {
    if (!videoEl) return;
    videoEl.muted = !listening;
    videoEl.volume = Math.min(1, Math.max(0, volume / 100));
  });

  async function pollStats(): Promise<void> {
    const pc = session?.pc;
    if (!pc) return;
    try {
      // Capture the pc once: onDestroy can null `session` (and close the pc) mid-await
      // when the input switches, so re-reading session.pc would throw. Re-check the
      // component is still alive before writing $state.
      const v = await inboundVideoStats(pc);
      if (!session) return;
      framesDecoded = v.framesDecoded;
      jitterBufferMs = v.jitterBufferMs;
      const a = await inboundAudioStats(pc);
      if (!session) return;
      audioTrack = a.hasTrack;
      audioPackets = a.packetsReceived;
    } catch {
      /* getStats() on a closing pc can reject — the stats are best-effort */
    }
  }

  onMount(() => {
    void (async () => {
      const el = videoEl;
      if (!el) return;
      let s: LiveSession;
      try {
        s = await connectCamera(camera.id);
      } catch (err) {
        if (destroyed) return;
        status = 'error';
        errorMsg = err instanceof Error ? err.message : 'could not connect to the stream';
        return;
      }
      if (destroyed) {
        s.pc.close();
        return;
      }
      session = s;
      el.srcObject = s.stream;
      status = 'live';
      statsTimer = setInterval(() => void pollStats(), 500);
    })();
  });

  onDestroy(() => {
    destroyed = true;
    if (statsTimer) clearInterval(statsTimer);
    session?.pc.close();
    session = null;
  });
</script>

<div class="stage">
  <video
    bind:this={videoEl}
    autoplay
    playsinline
    muted
    data-testid="live-video"
    data-frames={framesDecoded}
    data-latency-ms={jitterBufferMs === null ? '' : Math.round(jitterBufferMs)}
    data-audio-track={audioTrack ? '1' : '0'}
    data-audio-packets={audioPackets}
  ></video>

  <div
    class="badge"
    class:on={status === 'live' && framesDecoded > 0}
    data-testid="live-status"
    data-status={status}
    data-frames={framesDecoded}
  >
    {#if status === 'live' && framesDecoded > 0}
      ● LIVE
    {:else if status === 'error'}
      Signal unavailable
    {:else}
      Connecting…
    {/if}
  </div>

  {#if camera.has_audio}
    <div class="audio-controls">
      <button
        type="button"
        class="listen"
        class:on={listening}
        data-testid="listen-toggle"
        aria-pressed={listening}
        onclick={() => (listening = !listening)}
      >
        {listening ? '🔊 Listening' : '🔈 Listen in'}
      </button>
      {#if listening}
        <input
          type="range"
          class="volume"
          min="0"
          max="100"
          step="1"
          bind:value={volume}
          data-testid="camera-volume"
          aria-label="Volume"
        />
      {/if}
    </div>
  {/if}
</div>

{#if errorMsg}<p class="error" role="alert">{errorMsg}</p>{/if}

<style>
  .stage {
    position: relative;
    background: #000;
    aspect-ratio: 16 / 9;
    max-height: 70vh;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: var(--r);
    overflow: hidden;
  }
  video {
    width: 100%;
    height: 100%;
    object-fit: contain;
    background: #000;
  }
  .badge {
    position: absolute;
    top: var(--sp-2);
    left: var(--sp-2);
    font-size: var(--fs-xs);
    letter-spacing: 0.03em;
    padding: var(--sp-1) var(--sp-2);
    border-radius: var(--r-sm);
    background: rgba(0, 0, 0, 0.6);
    color: var(--overlay-ink);
  }
  .badge.on {
    color: var(--ok);
  }
  .audio-controls {
    position: absolute;
    bottom: var(--sp-2);
    right: var(--sp-2);
    left: var(--sp-2);
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: var(--sp-3);
  }
  .listen {
    min-height: var(--tap);
    font-size: var(--fs-sm);
    font-weight: 650;
    padding: var(--sp-2) var(--sp-4);
    border: none;
    border-radius: var(--r-pill);
    background: rgba(0, 0, 0, 0.6);
    color: var(--overlay-ink);
    cursor: pointer;
  }
  .listen.on {
    background: var(--accent);
    color: var(--accent-ink);
  }
  .volume {
    flex: 1;
    max-width: 12rem;
    accent-color: var(--accent);
    cursor: pointer;
  }
  .error {
    color: var(--danger);
    padding: var(--sp-3) var(--sp-4) 0;
    font-size: var(--fs-sm);
  }
</style>
