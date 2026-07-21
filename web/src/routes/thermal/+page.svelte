<script lang="ts">
  // Thermal view (Phase 8 / M8.2): the live heatmap of a paired thermal node, streamed
  // over /ws/thermal/{id}. Awareness only — warmth and presence, never anyone's
  // temperature, and not a medical or diagnostic tool (§2, §7.4). Any authenticated
  // household member can watch. The heatmap itself lives in <ThermalHeatmap>, shared with
  // the Live view's Thermal tab.
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import ThermalHeatmap from '$lib/ThermalHeatmap.svelte';
  import { fetchDevices, fetchSession, type Device, type User } from '$lib/api';

  let ready = $state(false);
  let user = $state<User | null>(null);
  let thermalDevices = $state<Device[]>([]);
  let selectedId = $state<number | null>(null);

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
      if (thermalDevices.length > 0) selectedId = thermalDevices[0].id;
      ready = true;
    })();
  });
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
              onclick={() => (selectedId = d.id)}>{d.name}</button
            >
          {/each}
        </div>
      {/if}

      {#if selectedId !== null}
        {#key selectedId}
          <ThermalHeatmap deviceId={selectedId} />
        {/key}
      {/if}
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
  .who {
    color: var(--text-muted);
    font-size: var(--fs-sm);
  }
</style>
