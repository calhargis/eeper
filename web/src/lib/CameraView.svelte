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
  import {
    exitNativeFullscreen,
    isNativeFullscreen,
    onFullscreenChange,
    pickFullscreenStrategy,
    requestNativeFullscreen,
  } from '$lib/fullscreen';

  let { camera }: { camera: Camera } = $props();

  type Status = 'connecting' | 'live' | 'error';
  let videoEl = $state<HTMLVideoElement | undefined>();
  let stageEl = $state<HTMLDivElement | undefined>();
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

  // Fullscreen. Native where the browser has the Element Fullscreen API; a CSS
  // viewport-filling stage on iPhone Safari, which doesn't (see $lib/fullscreen).
  let fullscreen = $state(false);
  let fauxFullscreen = $state(false);

  async function toggleFullscreen(): Promise<void> {
    if (fullscreen) {
      if (!fauxFullscreen) await exitNativeFullscreen();
      fauxFullscreen = false;
      fullscreen = false;
      return;
    }
    const strategy = pickFullscreenStrategy(
      stageEl,
      typeof document === 'undefined' ? null : document,
    );
    if (strategy === 'native' && stageEl && (await requestNativeFullscreen(stageEl))) {
      fullscreen = true; // fullscreenchange keeps this honest if the user escapes out
      return;
    }
    fauxFullscreen = true;
    fullscreen = true;
  }

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

  // Track fullscreen exits we didn't initiate (Esc, the system back gesture, the browser's
  // own exit button) so our button label and styling stay truthful.
  let stopFullscreenWatch: (() => void) | null = null;

  // Stop the page behind the fixed stage scrolling/rubber-banding in the CSS fallback.
  $effect(() => {
    if (typeof document === 'undefined') return;
    document.body.classList.toggle('fs-lock', fauxFullscreen);
    return () => document.body.classList.remove('fs-lock');
  });

  onMount(() => {
    stopFullscreenWatch = onFullscreenChange(() => {
      if (!fauxFullscreen) fullscreen = isNativeFullscreen(stageEl);
    });
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
    stopFullscreenWatch?.();
    // Switching inputs while fullscreen must not strand the browser in fullscreen on a
    // element that is about to be torn down.
    if (fullscreen && !fauxFullscreen) void exitNativeFullscreen();
    session?.pc.close();
    session = null;
  });
</script>

<svelte:window
  onkeydown={(e) => {
    // Esc leaves the CSS fallback (the browser already handles Esc for native fullscreen).
    if (e.key === 'Escape' && fauxFullscreen) void toggleFullscreen();
  }}
/>

<div class="stage" class:fs={fauxFullscreen} bind:this={stageEl} data-fullscreen={fullscreen}>
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

  <button
    type="button"
    class="fs-btn"
    data-testid="camera-fullscreen"
    aria-pressed={fullscreen}
    aria-label={fullscreen ? 'Exit fullscreen' : 'Fullscreen'}
    title={fullscreen ? 'Exit fullscreen' : 'Fullscreen'}
    onclick={() => void toggleFullscreen()}
  >
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      stroke-linecap="round"
      stroke-linejoin="round"
      aria-hidden="true"
    >
      {#if fullscreen}
        <path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5" />
      {:else}
        <path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" />
      {/if}
    </svg>
  </button>

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
  /* Fullscreen, both ways: the native API (desktop/Android/iPad) and the CSS fallback
     (.fs — iPhone Safari, which has no Element Fullscreen API). Both drop the letterboxed
     16:9 box and fill the screen; the video keeps object-fit: contain so nothing crops. */
  .stage:fullscreen,
  .stage.fs {
    aspect-ratio: auto;
    max-height: none;
    border-radius: 0;
  }
  .stage.fs {
    position: fixed;
    inset: 0;
    /* Above the app's fixed bottom tab bar (z-index 20). */
    z-index: 50;
    /* Respect the notch/home indicator when there's no browser chrome (installed PWA). */
    padding: env(safe-area-inset-top) env(safe-area-inset-right) env(safe-area-inset-bottom)
      env(safe-area-inset-left);
  }
  .fs-btn {
    position: absolute;
    top: var(--sp-2);
    right: var(--sp-2);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: var(--tap);
    height: var(--tap);
    padding: 0;
    border: none;
    border-radius: var(--r-pill);
    background: rgba(0, 0, 0, 0.6);
    color: var(--overlay-ink);
    cursor: pointer;
  }
  .fs-btn svg {
    width: 22px;
    height: 22px;
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
