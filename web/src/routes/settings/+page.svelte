<script lang="ts">
  // Settings (M4.3): an admin-only hub consolidating the account + the management
  // surfaces. Viewers ("grandparent mode") are scoped to Live + Tonight and are
  // redirected away from here. Notification preferences live on the Tonight view (so a
  // viewer can still manage their own), and this page links to them.
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { fetchPulseoxStatus, fetchSession, fetchStatus, type User } from '$lib/api';

  let ready = $state(false);
  let user = $state<User | null>(null);
  let version = $state('');
  let pulseoxProfile = $state(false);

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
        version = (await fetchStatus()).version;
        pulseoxProfile = (await fetchPulseoxStatus()).profile_enabled;
      } catch {
        pulseoxProfile = false;
      }
      ready = true;
    })();
  });
</script>

<svelte:head><title>eeper — settings</title></svelte:head>

{#if !ready}
  <p class="loading">Loading…</p>
{:else}
  <header>
    <a href="/" class="back" aria-label="Back">‹ eeper</a>
    <span class="title">Settings</span>
    {#if user}<span class="who">{user.username}</span>{/if}
  </header>

  <main data-testid="settings">
    <section class="card" data-testid="settings-account">
      <h2>Account</h2>
      <div class="row">
        <span class="k">Signed in as</span><span class="v">{user?.username}</span>
      </div>
      <div class="row"><span class="k">Role</span><span class="v">{user?.role}</span></div>
    </section>

    <section class="card">
      <h2>Manage</h2>
      <a class="link" href="/devices" data-testid="settings-devices"
        >Devices — pair &amp; monitor sensor nodes</a
      >
      <a class="link" href="/trends" data-testid="settings-trends"
        >Trends — sleep history &amp; CSV export</a
      >
      {#if pulseoxProfile}
        <a class="link" href="/pulseox" data-testid="settings-pulseox"
          >Pulse-ox — optional trend context</a
        >
      {/if}
      <a class="link" href="/tonight" data-testid="settings-notifications"
        >Notifications — configure on the Tonight view</a
      >
    </section>

    {#if version}<p class="version">eeper v{version}</p>{/if}
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
  .loading {
    text-align: center;
    margin: 3rem 1rem;
    color: #8a93a6;
  }
  main {
    max-width: 34rem;
    margin: 1rem auto;
    padding: 0 1rem;
  }
  .card {
    background: #101a2b;
    border: 1px solid #1b2537;
    border-radius: 0.5rem;
    padding: 1rem;
    margin-bottom: 1rem;
  }
  .card h2 {
    font-size: 0.95rem;
    margin: 0 0 0.6rem;
    color: #cbd5ea;
  }
  .row {
    display: flex;
    justify-content: space-between;
    padding: 0.25rem 0;
    font-size: 0.9rem;
  }
  .row .k {
    color: #8a93a6;
  }
  .link {
    display: block;
    padding: 0.55rem 0;
    color: #7fb0e8;
    text-decoration: none;
    font-size: 0.9rem;
    border-top: 1px solid #16202f;
  }
  .link:first-of-type {
    border-top: none;
  }
  .version {
    text-align: center;
    color: #8a93a6;
    font-size: 0.8rem;
  }
</style>
