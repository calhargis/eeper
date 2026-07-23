<script lang="ts">
  // Live view: a picker of every CONNECTED input — each camera, each thermal node, and
  // the room microphone — with a dedicated live view per input. The camera is the
  // primary input (listed first, selected by default). Only inputs with a real live
  // feed appear here (presence/motion sensors have no live stream — they live on the
  // Devices page). Any authenticated household member can watch; the route guards on
  // the session.
  import { onDestroy, onMount, tick } from 'svelte';
  import { goto } from '$app/navigation';
  import {
    fetchAudioAvailable,
    fetchCameras,
    fetchDevices,
    fetchSession,
    type Camera,
    type Device,
    type User,
  } from '$lib/api';
  import CameraView from '$lib/CameraView.svelte';
  import AudioMonitor from '$lib/AudioMonitor.svelte';
  import ThermalHeatmap from '$lib/ThermalHeatmap.svelte';
  import { camerasSignature, devicesSignature } from '$lib/live-inputs';

  type LiveInput =
    | { key: string; kind: 'camera'; label: string; camera: Camera }
    | { key: string; kind: 'thermal'; label: string; device: Device }
    | { key: string; kind: 'audio'; label: string };

  // Trusted, hardcoded SVG path constants (same pattern as the nav icons).
  const ICON: Record<LiveInput['kind'], string> = {
    camera: '<path d="M23 7l-7 5 7 5V7z"/><rect x="1" y="5" width="15" height="14" rx="3"/>',
    thermal: '<path d="M14 14V4a2 2 0 1 0-4 0v10a4 4 0 1 0 4 0z"/>',
    audio: '<path d="M4 10v4M8 6v12M12 3v18M16 7v10M20 10v4"/>',
  };

  let ready = $state(false);
  let user = $state<User | null>(null);
  let cameras = $state<Camera[]>([]);
  let thermalDevices = $state<Device[]>([]);
  let audioAvailable = $state(false);
  let selectedKey = $state<string | null>(null);
  let errorMsg = $state('');
  let healthTimer: ReturnType<typeof setInterval> | null = null;
  let destroyed = false;

  // Only CONNECTED inputs are offered (a camera/node reporting offline drops out).
  // Cameras first (the primary input), then thermal nodes, then the room mic.
  const inputs = $derived<LiveInput[]>([
    ...cameras
      .filter((c) => c.online !== false)
      .map((c): LiveInput => ({ key: `cam-${c.id}`, kind: 'camera', label: c.name, camera: c })),
    ...thermalDevices
      .filter((d) => d.online !== false)
      .map((d): LiveInput => ({
        key: `thermal-${d.id}`,
        kind: 'thermal',
        label: d.name,
        device: d,
      })),
    ...(audioAvailable ? [{ key: 'audio', kind: 'audio', label: 'Audio' } as LiveInput] : []),
  ]);
  const selected = $derived(inputs.find((i) => i.key === selectedKey) ?? inputs[0] ?? null);

  // If the selection falls away (input disconnected, or none chosen yet), settle on the
  // first input — which, with cameras listed first, keeps the camera as the default.
  $effect(() => {
    if (inputs.length > 0 && !inputs.some((i) => i.key === selectedKey)) {
      selectedKey = inputs[0].key;
    }
  });

  function statusOf(input: LiveInput): boolean | null {
    if (input.kind === 'camera') return input.camera.online;
    if (input.kind === 'thermal') return input.device.online;
    return true; // audio availability is already gated at the list level
  }

  function fmtChecked(iso: string | null): string {
    if (!iso) return 'not checked yet';
    const secs = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
    if (secs < 5) return 'just now';
    if (secs < 60) return `${secs}s ago`;
    return `${Math.round(secs / 60)}m ago`;
  }

  async function loadInputs(): Promise<void> {
    // Reassign state ONLY when a structural field changes — never on the last_checked /
    // last_seen_at heartbeat that advances every poll. Otherwise each 3s poll would churn
    // `inputs`/`selected` into new objects and tear the thermal WebSocket down + reconnect.
    try {
      const next = await fetchCameras();
      if (camerasSignature(next) !== camerasSignature(cameras)) cameras = next;
    } catch (err) {
      errorMsg = err instanceof Error ? err.message : 'could not load cameras';
    }
    try {
      const next = (await fetchDevices()).filter((d) => d.kind === 'thermal');
      if (devicesSignature(next) !== devicesSignature(thermalDevices)) thermalDevices = next;
    } catch {
      /* keep the previous thermalDevices on a failed poll (don't blank the picker) */
    }
  }

  onMount(() => {
    void (async () => {
      const session = await fetchSession();
      if (!session) {
        void goto('/'); // client-side guard: no session -> back to sign-in
        return;
      }
      user = session;
      ready = true;
      await tick();
      await loadInputs();
      audioAvailable = await fetchAudioAvailable();
      if (destroyed) return; // unmounted during the awaits — don't start an orphaned poll
      // Poll input health so a camera/node coming or going updates the picker live.
      healthTimer = setInterval(() => void loadInputs(), 3000);
    })();
  });

  onDestroy(() => {
    destroyed = true;
    if (healthTimer) clearInterval(healthTimer);
  });
</script>

<svelte:head>
  <title>eeper — live</title>
</svelte:head>

{#if !ready}
  <p class="loading">Loading…</p>
{:else}
  <header class="appbar">
    <a href="/" class="back" aria-label="Back">‹</a>
    <span class="title">eeper</span>
    <span class="spacer"></span>
    {#if user}<span class="who">{user.username}</span>{/if}
  </header>

  {#if inputs.length === 0}
    <p class="empty">No inputs are connected yet.</p>
  {:else}
    <div class="picker" role="group" aria-label="Inputs" data-testid="input-picker">
      {#each inputs as input (input.key)}
        {@const on = statusOf(input)}
        <button
          type="button"
          class="chip"
          class:active={selected?.key === input.key}
          aria-pressed={selected?.key === input.key}
          data-testid={`input-${input.key}`}
          onclick={() => (selectedKey = input.key)}
        >
          <!-- eslint-disable svelte/no-at-html-tags -- trusted, hardcoded icon path constants -->
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.8"
            stroke-linecap="round"
            stroke-linejoin="round"
            aria-hidden="true">{@html ICON[input.kind]}</svg
          >
          <!-- eslint-enable svelte/no-at-html-tags -->
          <span class="chip-label">{input.label}</span>
          {#if input.kind !== 'audio'}
            <span class="dot" class:online={on} class:offline={on === false}></span>
          {/if}
        </button>
      {/each}
    </div>

    <div class="view" data-testid="live-view" data-kind={selected?.kind}>
      {#key selected?.key}
        {#if selected?.kind === 'camera'}
          <CameraView camera={selected.camera} />
        {:else if selected?.kind === 'thermal'}
          <ThermalHeatmap deviceId={selected.device.id} />
        {:else if selected?.kind === 'audio'}
          <AudioMonitor />
        {/if}
      {/key}
    </div>

    {#if selected}
      <p class="meta">
        {#if selected.kind === 'camera'}
          <span
            class="dot"
            class:online={selected.camera.online}
            class:offline={selected.camera.online === false}
          ></span>
          <strong>{selected.camera.name}</strong>
          · {selected.camera.online
            ? 'online'
            : selected.camera.online === false
              ? 'offline'
              : 'checking'}
          <!-- Only show the last-check time when NOT online (where staleness is the point);
               while online it would just tick upward, since the poll no longer churns state. -->
          {#if selected.camera.online !== true}· {fmtChecked(selected.camera.last_checked)}{/if}
        {:else if selected.kind === 'thermal'}
          <span
            class="dot"
            class:online={selected.device.online}
            class:offline={selected.device.online === false}
          ></span>
          <strong>{selected.device.name}</strong>
          · {selected.device.online
            ? 'online'
            : selected.device.online === false
              ? 'offline'
              : 'checking'}
          {#if selected.device.online !== true}· {fmtChecked(selected.device.last_seen_at)}{/if}
        {:else}
          <span class="dot online"></span>
          <strong>Room microphone</strong> · live audio
        {/if}
      </p>
    {/if}
  {/if}

  {#if errorMsg}<p class="error" role="alert">{errorMsg}</p>{/if}
{/if}

<style>
  .who {
    color: var(--text-muted);
    font-size: var(--fs-sm);
  }
  .loading,
  .empty {
    text-align: center;
    color: var(--text-muted);
    margin: var(--sp-7) var(--sp-4);
  }
  /* The input picker — a horizontal, scrollable rail of icon chips. */
  .picker {
    display: flex;
    gap: var(--sp-2);
    padding: var(--sp-3) var(--sp-4);
    overflow-x: auto;
    scrollbar-width: none;
  }
  .picker::-webkit-scrollbar {
    display: none;
  }
  .chip {
    flex: 0 0 auto;
    display: inline-flex;
    align-items: center;
    gap: var(--sp-2);
    min-height: var(--tap);
    padding: var(--sp-2) var(--sp-4);
    border: 1px solid var(--border);
    border-radius: var(--r-pill);
    background: var(--surface);
    color: var(--text-2);
    font: inherit;
    font-weight: 650;
    font-size: var(--fs-sm);
    cursor: pointer;
    transition:
      background 0.15s ease,
      color 0.15s ease,
      border-color 0.15s ease;
  }
  .chip svg {
    width: 20px;
    height: 20px;
    flex: none;
  }
  .chip.active {
    background: var(--accent);
    color: var(--accent-ink);
    border-color: transparent;
  }
  .chip-label {
    white-space: nowrap;
  }
  .view {
    padding: 0 var(--sp-4);
  }
  .meta {
    display: flex;
    align-items: center;
    gap: var(--sp-2);
    padding: var(--sp-3) var(--sp-4) 0;
    color: var(--text-2);
    font-size: var(--fs-sm);
  }
  .dot {
    width: 0.6rem;
    height: 0.6rem;
    border-radius: var(--r-pill);
    background: var(--text-muted); /* unknown / checking */
    flex: none;
  }
  .dot.online {
    background: var(--ok);
  }
  .dot.offline {
    background: var(--danger);
  }
  .error {
    color: var(--danger);
    padding: 0 var(--sp-4);
    font-size: var(--fs-sm);
  }
</style>
