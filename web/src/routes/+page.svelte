<script lang="ts">
  // Phase 0 (M0.2) shell: the first-boot wizard + sign-in. It talks to the
  // same-origin API (/api/v1) behind the edge proxy; the session cookie is
  // httpOnly and sent automatically. Live/Tonight/Trends/etc. arrive later.
  import { onMount } from 'svelte';

  type User = { id: number; username: string; role: string };
  type View = 'loading' | 'first-boot' | 'login' | 'authed';

  const MIN_PASSWORD = 12;

  let view = $state<View>('loading');
  let version = $state('');
  let user = $state<User | null>(null);
  let username = $state('');
  let password = $state('');
  let confirm = $state('');
  let error = $state('');
  let busy = $state(false);

  function api(path: string, init?: RequestInit): Promise<Response> {
    return fetch(`/api/v1${path}`, {
      ...init,
      headers: { 'content-type': 'application/json', ...(init?.headers ?? {}) },
    });
  }

  async function detail(res: Response, fallback: string): Promise<string> {
    try {
      const body = (await res.json()) as { detail?: string };
      return body.detail ?? fallback;
    } catch {
      return fallback;
    }
  }

  function reset(): void {
    username = '';
    password = '';
    confirm = '';
    error = '';
  }

  async function refresh(): Promise<void> {
    const status = await api('/system/status');
    const body = (await status.json()) as { first_boot_required: boolean; version: string };
    version = body.version;
    if (body.first_boot_required) {
      view = 'first-boot';
      return;
    }
    const session = await api('/auth/session');
    if (session.ok) {
      user = (await session.json()) as User;
      view = 'authed';
    } else {
      view = 'login';
    }
  }

  onMount(() => {
    void refresh();
  });

  async function submitFirstBoot(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    error = '';
    if (password.length < MIN_PASSWORD) {
      error = `Password must be at least ${MIN_PASSWORD} characters.`;
      return;
    }
    if (password !== confirm) {
      error = 'Passwords do not match.';
      return;
    }
    busy = true;
    try {
      const res = await api('/system/first-boot', {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      });
      if (res.status === 201) {
        user = (await res.json()) as User;
        reset();
        view = 'authed';
      } else if (res.status === 409) {
        reset();
        error = 'Already initialized — please sign in.';
        view = 'login';
      } else {
        error = await detail(res, 'Could not create the admin account.');
      }
    } finally {
      busy = false;
    }
  }

  async function submitLogin(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    error = '';
    busy = true;
    try {
      const res = await api('/auth/login', {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      });
      if (res.ok) {
        user = (await res.json()) as User;
        reset();
        view = 'authed';
      } else {
        error = 'Invalid username or password.';
      }
    } finally {
      busy = false;
    }
  }

  async function logout(): Promise<void> {
    await api('/auth/logout', { method: 'POST' });
    user = null;
    reset();
    view = 'login';
  }
</script>

<svelte:head>
  <title>eeper</title>
</svelte:head>

<main>
  <h1>eeper</h1>

  {#if view === 'loading'}
    <p>Loading…</p>
  {:else if view === 'first-boot'}
    <h2>Create your admin account</h2>
    <p class="muted">First-time setup. There are no default credentials.</p>
    <form onsubmit={submitFirstBoot}>
      <label
        >Username<input
          bind:value={username}
          autocomplete="username"
          required
          minlength="3"
        /></label
      >
      <label
        >Password<input
          type="password"
          bind:value={password}
          autocomplete="new-password"
          required
          minlength={MIN_PASSWORD}
        /></label
      >
      <label
        >Confirm password<input
          type="password"
          bind:value={confirm}
          autocomplete="new-password"
          required
        /></label
      >
      <button type="submit" disabled={busy}>{busy ? 'Creating…' : 'Create admin'}</button>
    </form>
  {:else if view === 'login'}
    <h2>Sign in</h2>
    <form onsubmit={submitLogin}>
      <label>Username<input bind:value={username} autocomplete="username" required /></label>
      <label
        >Password<input
          type="password"
          bind:value={password}
          autocomplete="current-password"
          required
        /></label
      >
      <button type="submit" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
    </form>
  {:else if view === 'authed' && user}
    <h2>Signed in</h2>
    <p>You are signed in as <strong>{user.username}</strong> ({user.role}).</p>
    <p class="muted">Monitoring features arrive in the next phases.</p>
    <button type="button" onclick={logout}>Sign out</button>
  {/if}

  {#if error}
    <p class="error" role="alert">{error}</p>
  {/if}

  <footer>
    <p class="note">Not a medical device — a sleep-insight and awareness tool only.</p>
    {#if version}<p class="muted">v{version}</p>{/if}
  </footer>
</main>

<style>
  main {
    font-family:
      system-ui,
      -apple-system,
      sans-serif;
    max-width: 24rem;
    margin: 3rem auto;
    padding: 0 1rem;
    line-height: 1.5;
  }
  h1 {
    margin-bottom: 0.25rem;
  }
  form {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    margin-top: 1rem;
  }
  label {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    font-size: 0.9rem;
  }
  input {
    padding: 0.5rem;
    font-size: 1rem;
  }
  button {
    padding: 0.6rem;
    font-size: 1rem;
    cursor: pointer;
  }
  .muted {
    color: #666;
    font-size: 0.85rem;
  }
  .error {
    color: #a00;
    font-size: 0.9rem;
  }
  .note {
    color: #a00;
    font-size: 0.8rem;
  }
  footer {
    margin-top: 2rem;
    border-top: 1px solid #ddd;
    padding-top: 1rem;
  }
</style>
