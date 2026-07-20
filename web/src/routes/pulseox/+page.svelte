<script lang="ts">
  // Pulse-ox view (M4.2): OPTIONAL and INSIGHTS-ONLY. Shows the trend-context heart-rate
  // history when pulse-ox is fully enabled (Compose profile on AND an admin has
  // acknowledged the disclaimer), the disclaimer + acknowledge flow when awaiting an
  // admin, or an "off on this deployment" note otherwise. The accuracy caveat is rendered
  // on EVERY state below — pulse-ox is never a vital-sign readout or alarm.
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import BarChart from '$lib/BarChart.svelte';
  import {
    acknowledgePulseox,
    fetchPulseoxDisclaimer,
    fetchPulseoxStatus,
    fetchPulseoxTrend,
    fetchSession,
    type PulseOxDisclaimer,
    type PulseOxStatus,
    type PulseOxTrendPoint,
    type User,
  } from '$lib/api';

  let ready = $state(false);
  let user = $state<User | null>(null);
  let status = $state<PulseOxStatus | null>(null);
  let disclaimer = $state<PulseOxDisclaimer | null>(null);
  let trend = $state<PulseOxTrendPoint[]>([]);
  let error = $state('');
  let acking = $state(false);

  const isAdmin = $derived(user?.role === 'admin');
  const hourLabel = (iso: string): string =>
    new Date(iso).toLocaleTimeString([], { hour: 'numeric' });

  async function load(): Promise<void> {
    status = await fetchPulseoxStatus();
    if (status.enabled) {
      trend = await fetchPulseoxTrend();
    }
  }

  async function acknowledge(): Promise<void> {
    if (!disclaimer) return;
    acking = true;
    error = '';
    try {
      status = await acknowledgePulseox(disclaimer.version);
      if (status.enabled) trend = await fetchPulseoxTrend();
    } catch (e) {
      error = e instanceof Error ? e.message : 'could not acknowledge';
    } finally {
      acking = false;
    }
  }

  onMount(() => {
    void (async () => {
      const session = await fetchSession();
      if (!session) {
        void goto('/');
        return;
      }
      if (session.role !== 'admin') {
        // Grandparent mode: viewers are scoped to Live + Tonight only.
        void goto('/tonight');
        return;
      }
      user = session;
      try {
        // The disclaimer (and its caveat string) is always available so the caveat can
        // render on every state below.
        disclaimer = await fetchPulseoxDisclaimer();
        await load();
      } catch (e) {
        error = e instanceof Error ? e.message : 'could not load pulse-ox';
      }
      ready = true;
    })();
  });
</script>

<svelte:head><title>eeper — pulse-ox</title></svelte:head>

{#if !ready}
  <p class="loading">Loading…</p>
{:else}
  <header class="appbar">
    <a href="/" class="back" aria-label="Back">‹</a>
    <span class="title">Pulse-ox</span>
    <span class="spacer"></span>
    {#if user}<span class="who">{user.username}</span>{/if}
  </header>

  <main data-testid="pulseox">
    <!-- The accuracy caveat is present on EVERY pulse-ox state (M4.2 copy criterion). -->
    {#if disclaimer}
      <p class="caveat" data-testid="pulseox-caveat">{disclaimer.accuracy_caveat}</p>
    {/if}

    {#if !status?.profile_enabled}
      <p class="empty" data-testid="pulseox-off">
        Pulse-ox is optional and is turned off on this deployment. It stays fully inert until an
        operator enables the pulse-ox profile.
      </p>
    {:else if !status.acknowledged}
      <section class="disclaimer" data-testid="pulseox-disclaimer">
        <h2>Before you enable pulse-ox</h2>
        {#if disclaimer}<p class="text">{disclaimer.text}</p>{/if}
        {#if disclaimer}
          <a class="link" href={disclaimer.safe_sleep_url} target="_blank" rel="noreferrer"
            >Safe-sleep guidance</a
          >
        {/if}
        {#if isAdmin}
          <button
            class="ack"
            onclick={acknowledge}
            disabled={acking}
            data-testid="pulseox-acknowledge"
          >
            {acking ? 'Saving…' : 'I understand — enable pulse-ox insights'}
          </button>
        {:else}
          <p class="note">An admin must acknowledge this before pulse-ox insights appear.</p>
        {/if}
      </section>
    {:else}
      <section class="trend" data-testid="pulseox-trend">
        <h2>Heart-rate trend context</h2>
        {#if trend.length === 0}
          <p class="empty" data-testid="pulseox-trend-empty">
            No pulse-ox samples yet — trend context appears once a node reports quality-gated
            readings.
          </p>
        {:else}
          <BarChart
            values={trend.map((p) => p.hr_avg)}
            labels={trend.map((p) => hourLabel(p.hour))}
            color="#7a5bb0"
            label="Average heart rate per hour (trend context)"
            testid="pulseox-hr-chart"
            format={(v) => `${Math.round(v)} bpm avg`}
          />
        {/if}
      </section>
    {/if}

    {#if error}<p class="error" role="alert">{error}</p>{/if}
  </main>
{/if}

<style>
  .who {
    color: var(--text-muted);
    font-size: var(--fs-xs);
  }
  .loading,
  .empty {
    text-align: center;
    margin: var(--sp-5) var(--sp-4);
    color: var(--text-muted);
  }
  main {
    max-width: var(--maxw);
    margin: var(--sp-4) auto;
    padding: 0 var(--sp-4);
  }
  .caveat {
    margin: 0 0 var(--sp-5);
    padding: var(--sp-3) var(--sp-4);
    background: var(--warn-subtle);
    border: 1px solid var(--border);
    border-left: 3px solid var(--warn);
    border-radius: var(--r-sm);
    color: var(--text-2);
    font-size: var(--fs-xs);
  }
  .disclaimer {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--r);
    padding: var(--sp-4);
    box-shadow: var(--shadow-sm);
  }
  .disclaimer h2,
  .trend h2 {
    font-size: var(--fs-base);
    margin: 0 0 var(--sp-3);
    color: var(--text);
  }
  .disclaimer .text {
    color: var(--text-2);
    font-size: var(--fs-sm);
    line-height: 1.5;
    white-space: pre-line;
  }
  .link {
    display: inline-block;
    margin: var(--sp-3) 0;
    color: var(--accent);
    font-size: var(--fs-xs);
  }
  .note {
    color: var(--text-muted);
    font-size: var(--fs-xs);
    margin: var(--sp-3) 0 0;
  }
  .ack {
    display: block;
    width: 100%;
    min-height: var(--tap);
    margin-top: var(--sp-4);
    padding: var(--sp-3);
    background: var(--accent);
    color: var(--accent-ink);
    border: none;
    border-radius: var(--r-sm);
    font-size: var(--fs-sm);
    font-weight: 600;
    cursor: pointer;
  }
  .ack:disabled {
    opacity: 0.6;
    cursor: default;
  }
  .error {
    color: var(--danger);
    font-size: var(--fs-sm);
  }
</style>
