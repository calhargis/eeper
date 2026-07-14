<script lang="ts">
  // Devices view (M3.1): sensor-node onboarding. An admin pairs a node — the server
  // mints a per-device MQTT credential scoped to the node's own topic subtree — and the
  // username/password/topic shown here appear ONCE (the password is never stored), so the
  // operator must copy them into the firmware now. The list shows each node's derived
  // health (online / offline / never seen), refreshed on a timer. Any authenticated
  // household member can view; only an admin can pair or unpair. An input node — never a
  // medical device.
  import { onDestroy, onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import {
    fetchDevices,
    fetchSession,
    pairDevice,
    unpairDevice,
    type Device,
    type DeviceKind,
    type PairedDevice,
    type User,
  } from '$lib/api';

  const KINDS: { value: DeviceKind; label: string }[] = [
    { value: 'mmwave', label: 'mmWave presence' },
    { value: 'pir', label: 'PIR motion' },
    { value: 'other', label: 'Other sensor' },
  ];
  const REFRESH_MS = 15_000; // re-poll so an online node ageing past its heartbeat flips offline

  let ready = $state(false);
  let user = $state<User | null>(null);
  let devices = $state<Device[]>([]);
  let name = $state('');
  let kind = $state<DeviceKind>('mmwave');
  let paired = $state<PairedDevice | null>(null); // the just-minted credential, shown once
  let error = $state('');
  let busy = $state(false);
  let destroyed = false;
  let timer: ReturnType<typeof setInterval> | null = null;

  const isAdmin = $derived(user?.role === 'admin');

  async function load(): Promise<void> {
    try {
      devices = await fetchDevices();
    } catch (e) {
      error = e instanceof Error ? e.message : 'Could not load devices.';
    }
  }

  function status(d: Device): { text: string; cls: string } {
    if (d.online === null) return { text: 'Never seen', cls: 'unknown' };
    return d.online ? { text: 'Online', cls: 'online' } : { text: 'Offline', cls: 'offline' };
  }

  function lastSeen(iso: string | null): string {
    if (!iso) return 'no readings yet';
    return new Date(iso).toLocaleString([], {
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
    });
  }

  async function submitPair(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    error = '';
    busy = true;
    try {
      paired = await pairDevice(name.trim(), kind);
      name = '';
      await load();
    } catch (e) {
      error = e instanceof Error ? e.message : 'Could not pair the device.';
    } finally {
      busy = false;
    }
  }

  async function unpair(d: Device): Promise<void> {
    if (!confirm(`Unpair "${d.name}"? Its MQTT credential is revoked immediately.`)) return;
    error = '';
    busy = true;
    try {
      await unpairDevice(d.id);
      if (paired?.id === d.id) paired = null;
      await load();
    } catch (e) {
      error = e instanceof Error ? e.message : 'Could not unpair the device.';
    } finally {
      busy = false;
    }
  }

  async function copy(text: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      /* clipboard may be unavailable (insecure context / denied) — the value is on screen */
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
      await load();
      ready = true;
      timer = setInterval(() => {
        if (!destroyed) void load();
      }, REFRESH_MS);
    })();
  });

  onDestroy(() => {
    destroyed = true;
    if (timer) clearInterval(timer);
  });
</script>

<svelte:head><title>eeper — devices</title></svelte:head>

{#if !ready}
  <p class="loading">Loading…</p>
{:else}
  <header>
    <a href="/" class="back" aria-label="Back">‹ eeper</a>
    <span class="title">Devices</span>
    {#if user}<span class="who">{user.username}</span>{/if}
  </header>

  <main>
    {#if isAdmin}
      <section class="pair" data-testid="pair-form">
        <h2>Pair a new device</h2>
        <form onsubmit={submitPair}>
          <label
            >Name<input
              bind:value={name}
              placeholder="Crib mmWave"
              required
              maxlength="150"
            /></label
          >
          <label
            >Type<select bind:value={kind}>
              {#each KINDS as k (k.value)}<option value={k.value}>{k.label}</option>{/each}
            </select></label
          >
          <button type="submit" disabled={busy || name.trim().length === 0}>
            {busy ? 'Pairing…' : 'Pair device'}
          </button>
        </form>
      </section>

      {#if paired}
        <section class="creds" data-testid="paired-credentials" role="alert">
          <h3>Save these credentials now</h3>
          <p class="warn">
            The password is shown only once and is never stored. Enter it into
            <strong>{paired.name}</strong>'s firmware before leaving this page.
          </p>
          <dl>
            <dt>MQTT username</dt>
            <dd>
              <code data-testid="paired-username">{paired.mqtt_username}</code>
              <button type="button" class="ghost" onclick={() => copy(paired!.mqtt_username)}
                >Copy</button
              >
            </dd>
            <dt>MQTT password</dt>
            <dd>
              <code data-testid="paired-password">{paired.mqtt_password}</code>
              <button type="button" class="ghost" onclick={() => copy(paired!.mqtt_password)}
                >Copy</button
              >
            </dd>
            <dt>Publish topic</dt>
            <dd>
              <code data-testid="paired-topic">{paired.topic_prefix}&lt;metric&gt;</code>
              <button type="button" class="ghost" onclick={() => copy(paired!.topic_prefix)}
                >Copy</button
              >
            </dd>
          </dl>
          <button type="button" onclick={() => (paired = null)}>Done — I've saved them</button>
        </section>
      {/if}
    {/if}

    <section class="list">
      <h2>Paired devices</h2>
      {#if devices.length === 0}
        <p class="muted">No devices paired yet.</p>
      {:else}
        <ul>
          {#each devices as d (d.id)}
            <li data-testid={`device-${d.id}`}>
              <div class="meta">
                <span class="name">{d.name}</span>
                <span class="kind">{d.kind}</span>
                <span class="seen">Last seen: {lastSeen(d.last_seen_at)}</span>
              </div>
              <span
                class={`badge ${status(d).cls}`}
                data-testid={`device-${d.id}-status`}
                data-online={d.online === null ? '' : String(d.online)}>{status(d).text}</span
              >
              {#if isAdmin}
                <button
                  type="button"
                  class="ghost"
                  data-testid={`unpair-${d.id}`}
                  disabled={busy}
                  onclick={() => unpair(d)}>Unpair</button
                >
              {/if}
            </li>
          {/each}
        </ul>
      {/if}
    </section>

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
  .loading {
    text-align: center;
    margin: 3rem;
    color: #8a93a6;
  }
  main {
    max-width: 34rem;
    margin: 1rem auto;
    padding: 0 1rem;
    line-height: 1.5;
  }
  section {
    margin-bottom: 1.75rem;
  }
  h2 {
    font-size: 1.05rem;
    margin: 0 0 0.6rem;
  }
  form {
    display: flex;
    flex-wrap: wrap;
    gap: 0.6rem;
    align-items: flex-end;
  }
  label {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    font-size: 0.85rem;
    flex: 1 1 10rem;
  }
  input,
  select {
    padding: 0.5rem;
    font-size: 1rem;
    background: #131c2e;
    color: inherit;
    border: 1px solid #26314a;
    border-radius: 0.4rem;
  }
  button {
    padding: 0.55rem 0.9rem;
    font-size: 0.95rem;
    cursor: pointer;
    background: #2b6cb0;
    color: #fff;
    border: none;
    border-radius: 0.4rem;
  }
  button:disabled {
    opacity: 0.5;
    cursor: default;
  }
  button.ghost {
    background: #17233c;
    color: #cbd5ea;
    border: 1px solid #26314a;
    padding: 0.35rem 0.6rem;
    font-size: 0.8rem;
  }
  .creds {
    border: 1px solid #3a5a2a;
    background: #14200f;
    border-radius: 0.5rem;
    padding: 0.9rem 1rem;
  }
  .creds h3 {
    margin: 0 0 0.4rem;
  }
  .warn {
    color: #d7c98a;
    font-size: 0.85rem;
    margin: 0 0 0.75rem;
  }
  dl {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: 0.4rem 0.9rem;
    margin: 0 0 0.9rem;
    align-items: center;
  }
  dt {
    color: #8a93a6;
    font-size: 0.8rem;
  }
  dd {
    margin: 0;
    display: flex;
    align-items: center;
    gap: 0.5rem;
    flex-wrap: wrap;
  }
  code {
    background: #0b1220;
    border: 1px solid #26314a;
    border-radius: 0.3rem;
    padding: 0.2rem 0.4rem;
    font-size: 0.85rem;
    word-break: break-all;
  }
  ul {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }
  li {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    padding: 0.6rem 0.8rem;
    background: #101a2b;
    border: 1px solid #1b2537;
    border-radius: 0.5rem;
  }
  .meta {
    display: flex;
    flex-direction: column;
    gap: 0.1rem;
    margin-right: auto;
    min-width: 0;
  }
  .name {
    font-weight: 600;
  }
  .kind {
    color: #8a93a6;
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.03em;
  }
  .seen {
    color: #8a93a6;
    font-size: 0.78rem;
  }
  .badge {
    font-size: 0.78rem;
    font-weight: 600;
    padding: 0.2rem 0.5rem;
    border-radius: 999px;
    white-space: nowrap;
  }
  .badge.online {
    background: #123a1c;
    color: #7ee08a;
  }
  .badge.offline {
    background: #3a1c12;
    color: #e88a7e;
  }
  .badge.unknown {
    background: #23293a;
    color: #9aa4bb;
  }
  .muted {
    color: #8a93a6;
    font-size: 0.9rem;
  }
  .error {
    color: #ff8f8f;
    font-size: 0.9rem;
  }
</style>
