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
  <header>
    <a href="/" class="back" aria-label="Back">‹ eeper</a>
    <span class="title">Trends</span>
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
          color="#1f6f4a"
          label="Hours asleep per night"
          testid="sleep-chart"
          format={(v) => `${v.toFixed(1)} h`}
        />
      </section>

      <section class="chart">
        <h2>Wakes per night</h2>
        <BarChart
          values={nightly.map((n) => n.wakes)}
          color="#b0722b"
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
            color="#2b6cb0"
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
  header {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 0.9rem 1rem;
    border-bottom: 1px solid #1b2537;
  }
  .back {
    color: #7fb0e8;
    text-decoration: none;
    font-weight: 600;
  }
  .title {
    font-weight: 700;
  }
  .who {
    margin-left: auto;
    color: #8a93a6;
    font-size: 0.85rem;
  }
  .loading,
  .empty {
    text-align: center;
    margin: 3rem 1rem;
    color: #8a93a6;
  }
  main {
    max-width: 34rem;
    margin: 1rem auto;
    padding: 0 1rem;
  }
  .cards {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 0.6rem;
    margin-bottom: 1.5rem;
  }
  .card {
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
    padding: 0.7rem 0.9rem;
    background: #101a2b;
    border: 1px solid #1b2537;
    border-radius: 0.5rem;
  }
  .card .k {
    color: #8a93a6;
    font-size: 0.75rem;
  }
  .card .v {
    font-size: 1.35rem;
    font-weight: 700;
  }
  .chart {
    margin-bottom: 1.5rem;
  }
  .chart h2 {
    font-size: 0.95rem;
    margin: 0 0 0.4rem;
    color: #cbd5ea;
  }
  .export {
    display: inline-block;
    margin-top: 0.5rem;
    padding: 0.55rem 0.9rem;
    background: #17233c;
    color: #e8ecf5;
    border: 1px solid #26314a;
    border-radius: 0.4rem;
    text-decoration: none;
    font-size: 0.9rem;
  }
  .error {
    color: #ff8f8f;
    font-size: 0.9rem;
  }
</style>
