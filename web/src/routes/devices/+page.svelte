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
  <header class="appbar">
    <a href="/" class="back" aria-label="Back">‹</a>
    <span class="title">Devices</span>
    <span class="spacer"></span>
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
              {#if d.kind === 'thermal'}
                <a class="ghost" href="/thermal" data-testid={`thermal-link-${d.id}`}>Live view</a>
              {/if}
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
  .who {
    color: var(--text-muted);
    font-size: var(--fs-sm);
  }
  .loading {
    text-align: center;
    margin: var(--sp-7) auto;
    color: var(--text-muted);
  }
  main {
    max-width: var(--maxw);
    margin: var(--sp-4) auto;
    padding: 0 var(--sp-4);
    line-height: 1.5;
  }
  section {
    margin-bottom: var(--sp-6);
  }
  h2 {
    font-size: var(--fs-lg);
    margin: 0 0 var(--sp-3);
  }
  form {
    display: flex;
    flex-wrap: wrap;
    gap: var(--sp-3);
    align-items: flex-end;
  }
  label {
    display: flex;
    flex-direction: column;
    gap: var(--sp-1);
    font-size: var(--fs-sm);
    color: var(--text-2);
    flex: 1 1 10rem;
  }
  input,
  select {
    min-height: var(--tap);
    padding: 0 var(--sp-4);
    font-size: var(--fs-base);
    background: var(--surface-2);
    color: var(--text);
    border: 1px solid var(--border-hi);
    border-radius: var(--r-sm);
  }
  input:focus,
  select:focus {
    outline: none;
    border-color: var(--accent);
    box-shadow: var(--ring);
  }
  button {
    min-height: var(--tap);
    padding: 0 var(--sp-5);
    font-size: var(--fs-base);
    font-weight: 650;
    cursor: pointer;
    background: var(--accent);
    color: var(--accent-ink);
    border: 1px solid transparent;
    border-radius: var(--r-pill);
  }
  button:hover {
    background: var(--accent-strong);
  }
  button:disabled {
    opacity: 0.5;
    cursor: default;
  }
  button.ghost {
    background: transparent;
    color: var(--text-2);
    border: 1px solid var(--border-hi);
    padding: 0 var(--sp-4);
    font-size: var(--fs-sm);
  }
  button.ghost:hover {
    background: var(--surface-2);
  }
  .creds {
    border: 1px solid var(--border);
    background: var(--warn-subtle);
    border-radius: var(--r);
    padding: var(--sp-4);
    box-shadow: var(--shadow-sm);
  }
  .creds h3 {
    margin: 0 0 var(--sp-2);
  }
  .warn {
    color: var(--warn);
    font-size: var(--fs-sm);
    margin: 0 0 var(--sp-3);
  }
  dl {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: var(--sp-2) var(--sp-4);
    margin: 0 0 var(--sp-4);
    align-items: center;
  }
  dt {
    color: var(--text-muted);
    font-size: var(--fs-sm);
  }
  dd {
    margin: 0;
    display: flex;
    align-items: center;
    gap: var(--sp-2);
    flex-wrap: wrap;
  }
  code {
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: var(--r-sm);
    padding: var(--sp-1) var(--sp-2);
    font-size: var(--fs-sm);
    word-break: break-all;
  }
  ul {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: var(--sp-2);
  }
  li {
    display: flex;
    align-items: center;
    gap: var(--sp-3);
    min-height: var(--tap);
    padding: var(--sp-3) var(--sp-4);
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--r);
    box-shadow: var(--shadow-sm);
  }
  .meta {
    display: flex;
    flex-direction: column;
    gap: var(--sp-1);
    margin-right: auto;
    min-width: 0;
  }
  .name {
    font-weight: 600;
  }
  .kind {
    color: var(--text-muted);
    font-size: var(--fs-xs);
    text-transform: uppercase;
    letter-spacing: 0.03em;
  }
  .seen {
    color: var(--text-muted);
    font-size: var(--fs-xs);
  }
  .badge {
    font-size: var(--fs-xs);
    font-weight: 650;
    padding: 3px 10px;
    border-radius: var(--r-pill);
    white-space: nowrap;
    border: 1px solid transparent;
  }
  .badge.online {
    background: var(--accent-subtle);
    color: var(--ok);
  }
  .badge.offline {
    background: var(--danger-subtle);
    color: var(--danger);
  }
  .badge.unknown {
    background: var(--surface-2);
    color: var(--text-muted);
    border-color: var(--border);
  }
  .muted {
    color: var(--text-muted);
    font-size: var(--fs-sm);
  }
  .error {
    color: var(--danger);
    background: var(--danger-subtle);
    border-radius: var(--r-sm);
    padding: var(--sp-3) var(--sp-4);
    font-size: var(--fs-sm);
  }
</style>
