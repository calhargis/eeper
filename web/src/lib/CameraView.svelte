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
  import {
    cameraKey,
    cssTransform,
    fitScale,
    loadTransform,
    type CameraTransform,
  } from '$lib/camera-transform';

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
  let transformCleanup: (() => void) | undefined;
  let fullscreen = $state(false);

  // Orientation is a per-device display preference (see $lib/camera-transform). `transform`
  // is re-read on the `camera-transform` event so the Settings preview updates live while
  // the buttons are being pressed.
  // Bumped by the `camera-transform` event; reading it inside the derived is what makes the
  // preview update live while the Settings buttons are pressed, without CameraView having to
  // own the value. Deriving (rather than initialising once) also re-reads when the camera
  // prop itself changes.
  let transformVersion = $state(0);
  const transform: CameraTransform = $derived.by(() => {
    void transformVersion; // read it so the derived re-runs when the event fires
    return loadTransform(cameraKey(camera.id));
  });
  // A quarter turn swaps the video's bounding box, so it has to be scaled to keep fitting.
  // The factor depends on the CURRENT box, which changes with fullscreen and rotation of the
  // device itself — hence a ResizeObserver rather than a constant.
  let stageW = $state(0);
  let stageH = $state(0);
  const videoTransform = $derived(
    cssTransform(transform, fitScale(transform.rotation, stageW, stageH)),
  );
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
    // Keep the fit-scale honest across fullscreen toggles and device rotation.
    const ro = new ResizeObserver(([entry]) => {
      stageW = entry.contentRect.width;
      stageH = entry.contentRect.height;
    });
    if (stageEl) {
      // Seed from the current box as well as observing. ResizeObserver does fire an initial
      // callback, but if it ever didn't the fit-scale would stay at its 0-size fallback of 1
      // and a quarter-turned picture would silently overflow its box — cheap insurance.
      const r = stageEl.getBoundingClientRect();
      stageW = r.width;
      stageH = r.height;
      ro.observe(stageEl);
    }
    const onTransform = (e: Event) => {
      const id = (e as CustomEvent<{ cameraId: number }>).detail?.cameraId;
      if (id === undefined || id === camera.id) transformVersion += 1;
    };
    window.addEventListener('camera-transform', onTransform);
    transformCleanup = () => {
      ro.disconnect();
      window.removeEventListener('camera-transform', onTransform);
    };

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
    transformCleanup?.();
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
    style:transform={videoTransform}
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

  <!-- One control bar along the BOTTOM. The fullscreen button used to sit in the top-right
       corner, which on an iPhone lands under the status bar / clock: the OS wins the tap, so
       once you were fullscreen you could not get out. Bottom-anchored also puts everything in
       thumb reach one-handed, and the safe-area insets keep it clear of the notch in
       landscape and the home indicator in portrait. -->
  <div class="controls">
    <div class="controls__left">
      {#if camera.has_audio}
        <button
          type="button"
          class="ctl listen"
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
      {/if}
    </div>

    <button
      type="button"
      class="ctl fs-btn"
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
  </div>
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
    flex: none;
    width: var(--tap);
    padding: 0;
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
  /* The control bar. Anchored to the BOTTOM and inset by the safe area, so it clears the
     iPhone status bar/clock (which used to swallow taps on the corner fullscreen button),
     the notch in landscape, and the home indicator in portrait. */
  .controls {
    position: absolute;
    inset: auto 0 0 0;
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: var(--sp-3);
    padding: var(--sp-2);
    padding-right: max(var(--sp-2), env(safe-area-inset-right));
    padding-left: max(var(--sp-2), env(safe-area-inset-left));
    padding-bottom: max(var(--sp-2), env(safe-area-inset-bottom));
    /* A scrim rather than per-control backgrounds: keeps every label legible over a bright
       frame without boxing each one. pointer-events stay on the controls themselves so the
       gradient never eats a tap meant for the video. */
    background: linear-gradient(to top, rgba(0, 0, 0, 0.55), rgba(0, 0, 0, 0));
    pointer-events: none;
  }
  .controls > *,
  .controls__left > * {
    pointer-events: auto;
  }
  .controls__left {
    display: flex;
    align-items: center;
    gap: var(--sp-3);
    flex: 1;
    min-width: 0;
  }
  .ctl {
    min-height: var(--tap);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border: none;
    border-radius: var(--r-pill);
    background: rgba(0, 0, 0, 0.6);
    color: var(--overlay-ink);
    cursor: pointer;
  }
  .listen {
    font-size: var(--fs-sm);
    font-weight: 650;
    padding: var(--sp-2) var(--sp-4);
  }
  .listen.on {
    background: var(--accent);
    color: var(--accent-ink);
  }
  .volume {
    flex: 1;
    min-width: 4rem;
    max-width: 12rem;
    accent-color: var(--accent);
    cursor: pointer;
  }
  /* Landscape on a phone: very little vertical room, so shrink the bar rather than let it
     eat the picture. */
  @media (max-height: 480px) {
    .controls {
      padding-top: var(--sp-1);
      gap: var(--sp-2);
    }
    .listen {
      padding: var(--sp-1) var(--sp-3);
    }
  }
  .error {
    color: var(--danger);
    padding: var(--sp-3) var(--sp-4) 0;
    font-size: var(--fs-sm);
  }
</style>
