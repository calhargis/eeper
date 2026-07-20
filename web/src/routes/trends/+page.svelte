<script lang="ts">
  // Trends view (M4.1): nightly sleep-duration / wake-count charts, a week-over-week
  // summary, and (admin only) a CSV export. Reads the Trends API, which is backed by the
  // TimescaleDB continuous aggregate. Awareness metrics only — sleep durations and wake
  // counts, never a medical or vital-sign readout. Any household member can view; only an
  // admin can export.
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import BarChart from '$lib/BarChart.svelte';
  import {
    fetchSession,
    fetchTrendsNightly,
    fetchTrendsWeekly,
    type TrendNight,
    type TrendWeek,
    type User,
  } from '$lib/api';

  let ready = $state(false);
  let user = $state<User | null>(null);
  let nightly = $state<TrendNight[]>([]);
  let weekly = $state<TrendWeek[]>([]);
  let error = $state('');

  const isAdmin = $derived(user?.role === 'admin');
  const hours = (s: number): number => s / 3600;
  const hoursText = (s: number): string => `${(s / 3600).toFixed(1)} h`;

  // Headline metrics over the loaded window (most-recent night + 7-night averages).
  const last = $derived(nightly.length ? nightly[nightly.length - 1] : null);
  const recent = $derived(nightly.slice(-7));
  const avg = (xs: number[]): number => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0);
  const avgSleep = $derived(avg(recent.map((n) => hours(n.total_sleep_s))));
  const avgWakes = $derived(avg(recent.map((n) => n.wakes)));
  const longest = $derived(
    nightly.length ? Math.max(...nightly.map((n) => hours(n.longest_stretch_s))) : 0,
  );

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
        [nightly, weekly] = await Promise.all([fetchTrendsNightly(), fetchTrendsWeekly()]);
      } catch (e) {
        error = e instanceof Error ? e.message : 'could not load trends';
      }
      ready = true;
    })();
  });
</script>

<svelte:head><title>eeper — trends</title></svelte:head>

{#if !ready}
  <p class="loading">Loading…</p>
{:else}
  <header class="appbar">
    <a href="/" class="back" aria-label="Back">‹</a>
    <span class="title">Trends</span>
    <span class="spacer"></span>
    {#if user}<span class="who">{user.username}</span>{/if}
  </header>

  <main data-testid="trends">
    {#if nightly.length === 0}
      <p class="empty" data-testid="trends-empty">
        No sleep data yet — trends appear after the first nights are recorded.
      </p>
    {:else}
      <section class="cards">
        <div class="card">
          <span class="k">Last night</span>
          <span class="v">{last ? hoursText(last.total_sleep_s) : '—'}</span>
        </div>
        <div class="card">
          <span class="k">7-night avg</span>
          <span class="v">{avgSleep.toFixed(1)} h</span>
        </div>
        <div class="card">
          <span class="k">Avg wakes</span>
          <span class="v">{avgWakes.toFixed(1)}</span>
        </div>
        <div class="card">
          <span class="k">Longest stretch</span>
          <span class="v">{longest.toFixed(1)} h</span>
        </div>
      </section>

      <section class="chart">
        <h2>Sleep per night</h2>
        <BarChart
          values={nightly.map((n) => hours(n.total_sleep_s))}
          color="var(--ok)"
          label="Hours asleep per night"
          testid="sleep-chart"
          format={(v) => `${v.toFixed(1)} h`}
        />
      </section>

      <section class="chart">
        <h2>Wakes per night</h2>
        <BarChart
          values={nightly.map((n) => n.wakes)}
          color="var(--warn)"
          label="Awakenings per night"
          testid="wakes-chart"
          format={(v) => `${v} wakes`}
        />
      </section>

      {#if weekly.length > 0}
        <section class="chart">
          <h2>Week over week (avg sleep)</h2>
          <BarChart
            values={weekly.map((w) => hours(w.avg_sleep_s))}
            color="var(--accent)"
            label="Average hours asleep per week"
            testid="weekly-chart"
            format={(v) => `${v.toFixed(1)} h avg`}
          />
        </section>
      {/if}

      {#if isAdmin}
        <a
          class="export"
          href="/api/v1/trends/export.csv"
          download="eeper-sleep-trends.csv"
          data-testid="export-csv">Export CSV</a
        >
      {/if}
    {/if}

    {#if error}<p class="error" role="alert">{error}</p>{/if}
  </main>
{/if}

<style>
  .who {
    margin-left: auto;
    color: var(--text-muted);
    font-size: var(--fs-xs);
  }
  .loading,
  .empty {
    text-align: center;
    margin: var(--sp-6) var(--sp-4);
    color: var(--text-muted);
  }
  main {
    max-width: var(--maxw);
    margin: var(--sp-4) auto;
    padding: 0 var(--sp-4);
  }
  .cards {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: var(--sp-3);
    margin-bottom: var(--sp-5);
  }
  .card {
    display: flex;
    flex-direction: column;
    gap: var(--sp-1);
    padding: var(--sp-3) var(--sp-4);
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--r-sm);
  }
  .card .k {
    color: var(--text-muted);
    font-size: var(--fs-xs);
  }
  .card .v {
    font-size: var(--fs-xl);
    font-weight: 700;
  }
  .chart {
    margin-bottom: var(--sp-5);
  }
  .chart h2 {
    font-size: var(--fs-sm);
    margin: 0 0 var(--sp-2);
    color: var(--text-2);
  }
  .export {
    display: inline-block;
    min-height: var(--tap);
    margin-top: var(--sp-2);
    padding: var(--sp-3) var(--sp-4);
    background: var(--surface-2);
    color: var(--text);
    border: 1px solid var(--border-hi);
    border-radius: var(--r-sm);
    text-decoration: none;
    font-size: var(--fs-sm);
  }
  .error {
    color: var(--danger);
    font-size: var(--fs-sm);
  }
</style>
