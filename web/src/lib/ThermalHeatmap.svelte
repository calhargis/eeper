<script lang="ts">
  // A live thermal heatmap for one paired node (Phase 8 / M8.2). Subscribes the device's
  // 32×24 grid over /ws/thermal/{id} and renders a RELATIVE false-color heatmap — brighter
  // is warmer, per-frame normalized. It shows warmth and presence, never anyone's
  // temperature, and is not a medical or diagnostic tool (§2, §7.4). Reused by the Thermal
  // view and the Live view's Thermal tab.
  import { subscribeToThermal, THERMAL_COLS, THERMAL_ROWS, type ThermalFrame } from '$lib/thermal';
  import {
    exitNativeFullscreen,
    isNativeFullscreen,
    onFullscreenChange,
    pickFullscreenStrategy,
    requestNativeFullscreen,
  } from '$lib/fullscreen';
  import OrientationControls from '$lib/OrientationControls.svelte';
  import {
    cssTransform,
    fitScale,
    loadTransform,
    thermalKey,
    type CameraTransform,
  } from '$lib/camera-transform';

  let { deviceId }: { deviceId: number } = $props();

  let connected = $state(false);
  let frame = $state<ThermalFrame | null>(null);
  let canvas = $state<HTMLCanvasElement | undefined>(undefined);
  let stageEl = $state<HTMLDivElement | undefined>(undefined);

  // Fullscreen. Native where the browser has the Element Fullscreen API; a CSS
  // viewport-filling stage on iPhone Safari, which doesn't (see $lib/fullscreen). A canvas
  // has no native video-fullscreen path at all, so the fallback matters here.
  let fullscreen = $state(false);

  // Orientation. A thermal node is mounted over a crib just like a camera and is just as
  // easy to fit sideways, so it gets the same controls — keyed separately, because a camera
  // and a node can share a numeric id.
  let transformVersion = $state(0);
  let stageW = $state(0);
  let stageH = $state(0);
  const transform: CameraTransform = $derived.by(() => {
    void transformVersion;
    return loadTransform(thermalKey(deviceId));
  });
  const canvasTransform = $derived(
    cssTransform(transform, fitScale(transform.rotation, stageW, stageH)),
  );

  // Track the stage box (the quarter-turn fit factor depends on it) and re-read when the
  // orientation buttons fire. $effect returns its own teardown, so both unwind on unmount.
  $effect(() => {
    const el = stageEl;
    if (!el) return;
    const r = el.getBoundingClientRect();
    stageW = r.width;
    stageH = r.height;
    const ro = new ResizeObserver(([entry]) => {
      stageW = entry.contentRect.width;
      stageH = entry.contentRect.height;
    });
    ro.observe(el);
    const onTransform = (e: Event) => {
      const k = (e as CustomEvent<{ storageKey?: string }>).detail?.storageKey;
      if (k === undefined || k === thermalKey(deviceId)) transformVersion += 1;
    };
    window.addEventListener('camera-transform', onTransform);
    return () => {
      ro.disconnect();
      window.removeEventListener('camera-transform', onTransform);
    };
  });
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
      fullscreen = true;
      return;
    }
    fauxFullscreen = true;
    fullscreen = true;
  }

  // Keep our state truthful when the user leaves fullscreen by Esc / system gesture, and
  // never strand the browser in fullscreen if this view unmounts (e.g. input switch).
  $effect(() => {
    const stop = onFullscreenChange(() => {
      if (!fauxFullscreen) fullscreen = isNativeFullscreen(stageEl);
    });
    return () => {
      stop();
      if (fullscreen && !fauxFullscreen) void exitNativeFullscreen();
    };
  });

  // Stop the page behind the fixed stage scrolling/rubber-banding in the CSS fallback.
  $effect(() => {
    if (typeof document === 'undefined') return;
    document.body.classList.toggle('fs-lock', fauxFullscreen);
    return () => document.body.classList.remove('fs-lock');
  });

  // A warm object standing clear of the background reads as presence — never a value.
  const presence = $derived(frame !== null && frame.t_max - frame.t_mean > 2.0);

  // A perceptual "thermal" colormap (inferno-like): cool = dark, warm = bright.
  const STOPS: [number, [number, number, number]][] = [
    [0.0, [0, 0, 4]],
    [0.2, [40, 11, 84]],
    [0.4, [101, 21, 110]],
    [0.5, [159, 42, 99]],
    [0.65, [212, 72, 66]],
    [0.8, [245, 125, 21]],
    [0.9, [250, 193, 39]],
    [1.0, [252, 255, 164]],
  ];
  function heat(v: number): [number, number, number] {
    const t = Math.max(0, Math.min(1, v));
    for (let i = 1; i < STOPS.length; i++) {
      if (t <= STOPS[i][0]) {
        const [t0, c0] = STOPS[i - 1];
        const [t1, c1] = STOPS[i];
        const f = (t - t0) / (t1 - t0 || 1);
        return [
          c0[0] + (c1[0] - c0[0]) * f,
          c0[1] + (c1[1] - c0[1]) * f,
          c0[2] + (c1[2] - c0[2]) * f,
        ];
      }
    }
    return STOPS[STOPS.length - 1][1];
  }

  const BUF_SCALE = 12; // draw the 32×24 grid into a 384×288 buffer, smoothed for the view
  function render(cv: HTMLCanvasElement, f: ThermalFrame): void {
    const ctx = cv.getContext('2d');
    if (!ctx) return;
    const off = document.createElement('canvas');
    off.width = THERMAL_COLS;
    off.height = THERMAL_ROWS;
    const octx = off.getContext('2d');
    if (!octx) return;
    const img = octx.createImageData(THERMAL_COLS, THERMAL_ROWS);
    const span = f.t_max - f.t_min || 1; // relative normalization — a heatmap, not a scale
    for (let i = 0; i < f.grid.length; i++) {
      const [r, g, b] = heat((f.grid[i] - f.t_min) / span);
      const p = i * 4;
      img.data[p] = r;
      img.data[p + 1] = g;
      img.data[p + 2] = b;
      img.data[p + 3] = 255;
    }
    octx.putImageData(img, 0, 0);
    cv.width = THERMAL_COLS * BUF_SCALE;
    cv.height = THERMAL_ROWS * BUF_SCALE;
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(off, 0, 0, cv.width, cv.height);
  }

  // (Re)subscribe whenever the device changes; the cleanup closes the previous stream.
  $effect(() => {
    const id = deviceId;
    connected = false;
    frame = null;
    const stream = subscribeToThermal(
      id,
      (f) => (frame = f),
      (c) => (connected = c),
    );
    return () => stream.close();
  });

  // Redraw whenever a new frame lands and the canvas is mounted.
  $effect(() => {
    if (frame && canvas) render(canvas, frame);
  });
</script>

<svelte:window
  onkeydown={(e) => {
    // Esc leaves the CSS fallback (the browser already handles Esc for native fullscreen).
    if (e.key === 'Escape' && fauxFullscreen) void toggleFullscreen();
  }}
/>

<div class="stage" class:fs={fauxFullscreen} bind:this={stageEl} data-fullscreen={fullscreen}>
  <canvas
    bind:this={canvas}
    style:transform={canvasTransform}
    class="heat"
    data-testid="thermal-canvas"
    aria-label="Live thermal heatmap"
  ></canvas>
  {#if !frame}
    <div class="overlay" data-testid="thermal-connecting">
      {connected ? 'Waiting for the first frame…' : 'Connecting…'}
    </div>
  {/if}
  <!-- One bottom bar, inset by the safe area: a corner button lands under the iPhone status
       bar, where the OS wins the tap and fullscreen becomes a one-way trip. -->
  <div class="controls">
    <OrientationControls storageKey={thermalKey(deviceId)} label="Thermal orientation" />
    <button
      type="button"
      class="ctl fs-btn"
      data-testid="thermal-fullscreen"
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

<div class="status">
  <span
    class="pill {presence ? 'pill--ok' : ''}"
    data-testid="thermal-presence"
    data-present={presence ? '1' : '0'}
  >
    {presence ? 'Presence' : 'No presence'}
  </span>
  <span class="pill {connected ? 'pill--ok' : 'pill--warn'}">
    {connected ? 'Live' : 'Reconnecting…'}
  </span>
</div>

<p class="caveat" data-testid="thermal-caveat">
  A relative view of warmth for awareness — brighter is warmer. It shows the scene and presence, <strong
    >not anyone's temperature</strong
  >, and is not a medical or diagnostic tool.
</p>

<style>
  .stage {
    position: relative;
    padding: var(--sp-2);
    display: flex;
    justify-content: center;
    align-items: center;
    background: #000;
    border-radius: var(--r);
    overflow: hidden;
  }
  .heat {
    width: 100%;
    max-width: 30rem;
    aspect-ratio: 4 / 3;
    border-radius: var(--r-sm);
    image-rendering: auto;
    display: block;
  }
  /* Fullscreen, both ways: the native API (desktop/Android/iPad) and the CSS fallback
     (.fs — iPhone Safari, which has no Element Fullscreen API). The heatmap grows to fill
     the screen while keeping its 4:3 grid ratio. */
  .stage:fullscreen,
  .stage.fs {
    border-radius: 0;
    padding: 0;
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
  .stage:fullscreen .heat,
  .stage.fs .heat {
    width: auto;
    max-width: 100%;
    height: 100%;
    max-height: 100%;
    border-radius: 0;
  }
  .controls {
    position: absolute;
    inset: auto 0 0 0;
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: var(--sp-2);
    padding: var(--sp-2);
    padding-right: max(var(--sp-2), env(safe-area-inset-right));
    padding-left: max(var(--sp-2), env(safe-area-inset-left));
    padding-bottom: max(var(--sp-2), env(safe-area-inset-bottom));
    background: linear-gradient(to top, rgba(0, 0, 0, 0.55), rgba(0, 0, 0, 0));
    pointer-events: none;
  }
  .controls > :global(*) {
    pointer-events: auto;
  }
  :global(.ctl) {
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
  .fs-btn {
    flex: none;
    width: var(--tap);
    padding: 0;
  }
  .fs-btn svg {
    width: 22px;
    height: 22px;
  }
  .overlay {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--overlay-ink);
    background: color-mix(in srgb, #000 55%, transparent);
    font-size: var(--fs-sm);
  }
  .status {
    display: flex;
    align-items: center;
    gap: var(--sp-2);
    margin-top: var(--sp-3);
  }
  .caveat {
    margin-top: var(--sp-4);
    padding: var(--sp-3) var(--sp-4);
    background: var(--warn-subtle);
    border-radius: var(--r-sm);
    color: var(--text-2);
    font-size: var(--fs-sm);
    line-height: 1.5;
  }
</style>
