<script lang="ts">
  // App shell for every route: dark night theme + the persistent safety
  // disclaimer (the product is a monitor, never a medical device).
  let { children } = $props();
</script>

{@render children()}

<footer class="safety">Not a medical device — a sleep-insight and awareness tool only.</footer>

<style>
  :global(html),
  :global(body) {
    margin: 0;
    background: #0b1220;
    color: #e8ecf5;
    font-family:
      system-ui,
      -apple-system,
      sans-serif;
    -webkit-font-smoothing: antialiased;
  }
  .safety {
    text-align: center;
    color: #8a93a6;
    font-size: 0.78rem;
    padding: 0.75rem 1rem;
  }
</style>
