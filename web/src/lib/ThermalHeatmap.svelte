<script lang="ts">
  // A live thermal heatmap for one paired node (Phase 8 / M8.2). Subscribes the device's
  // 32×24 grid over /ws/thermal/{id} and renders a RELATIVE false-color heatmap — brighter
  // is warmer, per-frame normalized. It shows warmth and presence, never anyone's
  // temperature, and is not a medical or diagnostic tool (§2, §7.4). Reused by the Thermal
  // view and the Live view's Thermal tab.
  import { subscribeToThermal, THERMAL_COLS, THERMAL_ROWS, type ThermalFrame } from '$lib/thermal';

  let { deviceId }: { deviceId: number } = $props();

  let connected = $state(false);
  let frame = $state<ThermalFrame | null>(null);
  let canvas = $state<HTMLCanvasElement | undefined>(undefined);

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

<div class="stage">
  <canvas
    bind:this={canvas}
    class="heat"
    data-testid="thermal-canvas"
    aria-label="Live thermal heatmap"
  ></canvas>
  {#if !frame}
    <div class="overlay" data-testid="thermal-connecting">
      {connected ? 'Waiting for the first frame…' : 'Connecting…'}
    </div>
  {/if}
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
