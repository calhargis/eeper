<script lang="ts">
  // Phase 0 placeholder shell. Live view, Tonight, Trends, Devices and Settings
  // arrive in later phases (see IMPLEMENTATION_PLAN.md).
  const version = '0.0.0';
</script>

<svelte:head>
  <title>eeper</title>
</svelte:head>

<main>
  <h1>eeper</h1>
  <p>Self-hosted baby monitoring. Pre-alpha skeleton (v{version}).</p>
  <p class="note">Not a medical device. A sleep-insight and awareness tool only.</p>
</main>

<style>
  main {
    font-family:
      system-ui,
      -apple-system,
      sans-serif;
    max-width: 40rem;
    margin: 4rem auto;
    padding: 0 1rem;
    line-height: 1.5;
  }
  h1 {
    margin-bottom: 0.25rem;
  }
  .note {
    color: #a00;
    font-size: 0.9rem;
  }
</style>
