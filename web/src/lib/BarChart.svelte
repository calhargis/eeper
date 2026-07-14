<script lang="ts">
  // A minimal dependency-free SVG bar chart (one bar per value). Used by the Trends
  // view; each bar carries a <title> tooltip and a data-testid for the harness.
  let {
    values,
    labels = [],
    color = '#2b6cb0',
    label = '',
    testid = 'chart',
    format = (v: number) => v.toFixed(1),
  }: {
    values: number[];
    // Optional per-bar labels (e.g. the hour) prepended to each bar's tooltip.
    labels?: string[];
    color?: string;
    label?: string;
    testid?: string;
    format?: (v: number) => string;
  } = $props();

  const W = 100;
  const H = 36;
  const gap = 0.18;
  const max = $derived(Math.max(1, ...values));
  const barW = $derived(values.length > 0 ? W / values.length : W);
</script>

<svg
  class="bars"
  viewBox="0 0 {W} {H}"
  preserveAspectRatio="none"
  data-testid={testid}
  role="img"
  aria-label={label}
>
  {#each values as v, i (i)}
    <rect
      data-testid={`${testid}-bar`}
      x={i * barW + (barW * gap) / 2}
      y={H - (Math.max(0, v) / max) * H}
      width={barW * (1 - gap)}
      height={(Math.max(0, v) / max) * H}
      rx="0.4"
      fill={color}
    >
      <title>{labels[i] ? `${labels[i]} — ${format(v)}` : format(v)}</title>
    </rect>
  {/each}
</svg>

<style>
  .bars {
    display: block;
    width: 100%;
    height: 4.5rem;
  }
</style>
