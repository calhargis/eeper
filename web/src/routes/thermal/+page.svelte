<script lang="ts">
  // Thermal view (Phase 8 / M8.2): a live, relative false-color heatmap of a paired
  // thermal node's 32×24 grid, streamed over /ws/thermal/{id}. Awareness only — it shows
  // warmth and presence, never anyone's temperature, and is not a medical or diagnostic
  // tool (§2, §7.4). Any authenticated household member can watch.
  import { onDestroy, onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { fetchDevices, fetchSession, type Device, type User } from '$lib/api';
  import {
    subscribeToThermal,
    THERMAL_COLS,
    THERMAL_ROWS,
    type ThermalFrame,
    type ThermalStream,
  } from '$lib/thermal';

  let ready = $state(false);
  let user = $state<User | null>(null);
  let thermalDevices = $state<Device[]>([]);
  let selectedId = $state<number | null>(null);
  let connected = $state(false);
  let frame = $state<ThermalFrame | null>(null);
  let canvas = $state<HTMLCanvasElement | undefined>(undefined);
  let stream: ThermalStream | null = null;

  // A warm object standing clear of the background reads as presence — never a value.
  const presence = $derived(frame !== null && frame.t_max - frame.t_mean > 2.0);
  const selected = $derived(thermalDevices.find((d) => d.id === selectedId) ?? null);

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

  // Redraw whenever a new frame lands and the canvas is mounted.
  $effect(() => {
    if (frame && canvas) render(canvas, frame);
  });

  function openStream(id: number): void {
    stream?.close();
    connected = false;
    frame = null;
    stream = subscribeToThermal(
      id,
      (f) => (frame = f),
      (c) => (connected = c),
    );
  }

  function select(id: number): void {
    selectedId = id;
    openStream(id);
  }

  onMount(() => {
    void (async () => {
      const session = await fetchSession();
      if (!session) {
        void goto('/');
        return;
      }
      user = session;
      try {
        thermalDevices = (await fetchDevices()).filter((d) => d.kind === 'thermal');
      } catch {
        thermalDevices = [];
      }
      if (thermalDevices.length > 0) select(thermalDevices[0].id);
      ready = true;
    })();
  });

  onDestroy(() => stream?.close());
</script>

<svelte:head><title>eeper — thermal</title></svelte:head>

{#if !ready}
  <p class="loading">Loading…</p>
{:else}
  <header class="appbar">
    <a href="/" class="back" aria-label="Back">‹</a>
    <span class="title">Thermal</span>
    <span class="spacer"></span>
    {#if user}<span class="who">{user.username}</span>{/if}
  </header>

  <main data-testid="thermal">
    {#if thermalDevices.length === 0}
      <p class="empty" data-testid="thermal-empty">
        No thermal node paired. Pair one under <a href="/devices">Devices</a> to see its live view.
      </p>
    {:else}
      {#if thermalDevices.length > 1}
        <div class="picker">
          {#each thermalDevices as d (d.id)}
            <button
              type="button"
              class="chip"
              class:active={d.id === selectedId}
              onclick={() => select(d.id)}>{d.name}</button
            >
          {/each}
        </div>
      {/if}

      <div class="stage card">
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
        {#if selected}<span class="dev muted">{selected.name}</span>{/if}
      </div>

      <p class="caveat" data-testid="thermal-caveat">
        A relative view of warmth for awareness — brighter is warmer. It shows the scene and
        presence, <strong>not anyone's temperature</strong>, and is not a medical or diagnostic
        tool.
      </p>
    {/if}
  </main>
{/if}

<style>
  main {
    max-width: var(--maxw);
    margin: var(--sp-4) auto;
    padding: 0 var(--sp-4);
  }
  .loading,
  .empty {
    text-align: center;
    color: var(--text-muted);
    margin: var(--sp-7) var(--sp-4);
  }
  .picker {
    display: flex;
    flex-wrap: wrap;
    gap: var(--sp-2);
    margin-bottom: var(--sp-3);
  }
  .chip {
    min-height: var(--tap);
    padding: 0 var(--sp-4);
    border-radius: var(--r-pill);
    border: 1px solid var(--border);
    background: var(--surface-2);
    color: var(--text-2);
    font: inherit;
    font-weight: 650;
    cursor: pointer;
  }
  .chip.active {
    background: var(--accent);
    color: var(--accent-ink);
    border-color: transparent;
  }
  .stage {
    position: relative;
    padding: var(--sp-2);
    display: flex;
    justify-content: center;
    align-items: center;
    background: #000;
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
    color: var(--text-2);
    background: color-mix(in srgb, #000 55%, transparent);
    font-size: var(--fs-sm);
  }
  .status {
    display: flex;
    align-items: center;
    gap: var(--sp-2);
    margin-top: var(--sp-3);
  }
  .dev {
    margin-left: auto;
    font-size: var(--fs-sm);
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
  .who {
    color: var(--text-muted);
    font-size: var(--fs-sm);
  }
</style>
