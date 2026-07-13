<script lang="ts">
  // First-boot wizard + sign-in. Once authed, the Live view is one tap away.
  import { onMount } from 'svelte';
  import { api, detail, fetchSession, fetchStatus, type User } from '$lib/api';

  type LoginResult = { totp_required: boolean; challenge: string | null; user: User | null };
  type View = 'loading' | 'first-boot' | 'login' | 'totp' | 'authed';

  const MIN_PASSWORD = 12;

  let view = $state<View>('loading');
  let version = $state('');
  let user = $state<User | null>(null);
  let username = $state('');
  let password = $state('');
  let confirm = $state('');
  let challenge = $state('');
  let totpCode = $state('');
  let error = $state('');
  let busy = $state(false);

  function reset(): void {
    username = '';
    password = '';
    confirm = '';
    error = '';
  }

  async function refresh(): Promise<void> {
    const status = await fetchStatus();
    version = status.version;
    if (status.first_boot_required) {
      view = 'first-boot';
      return;
    }
    const session = await fetchSession();
    if (session) {
      user = session;
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
      if (res.status === 429) {
        error = 'Too many failed attempts — try again later.';
        return;
      }
      if (!res.ok) {
        error = 'Invalid username or password.';
        return;
      }
      const body = (await res.json()) as LoginResult;
      if (body.totp_required && body.challenge) {
        challenge = body.challenge;
        totpCode = '';
        view = 'totp';
      } else if (body.user) {
        user = body.user;
        reset();
        view = 'authed';
      }
    } finally {
      busy = false;
    }
  }

  async function submitTotp(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    error = '';
    busy = true;
    try {
      const res = await api('/auth/totp/verify', {
        method: 'POST',
        body: JSON.stringify({ challenge, code: totpCode }),
      });
      if (res.ok) {
        const body = (await res.json()) as LoginResult;
        user = body.user;
        reset();
        challenge = '';
        totpCode = '';
        view = 'authed';
      } else {
        error = 'Invalid code.';
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
  {:else if view === 'totp'}
    <h2>Two-factor code</h2>
    <p class="muted">Enter the 6-digit code from your authenticator app.</p>
    <form onsubmit={submitTotp}>
      <label
        >Code<input
          bind:value={totpCode}
          inputmode="numeric"
          autocomplete="one-time-code"
          required
        /></label
      >
      <button type="submit" disabled={busy}>{busy ? 'Verifying…' : 'Verify'}</button>
    </form>
  {:else if view === 'authed' && user}
    <h2>Signed in</h2>
    <p>You are signed in as <strong>{user.username}</strong> ({user.role}).</p>
    <a class="cta" href="/live">Open live view</a>
    <a class="cta secondary" href="/tonight">Tonight</a>
    <a class="cta secondary" href="/trends">Trends</a>
    <a class="cta secondary" href="/devices">Devices</a>
    <button type="button" onclick={logout}>Sign out</button>
  {/if}

  {#if error}
    <p class="error" role="alert">{error}</p>
  {/if}

  {#if version}<p class="version">v{version}</p>{/if}
</main>

<style>
  main {
    max-width: 24rem;
    margin: 3rem auto 1rem;
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
    background: #131c2e;
    color: inherit;
    border: 1px solid #26314a;
    border-radius: 0.4rem;
  }
  button {
    padding: 0.6rem;
    font-size: 1rem;
    cursor: pointer;
    background: #2b6cb0;
    color: #fff;
    border: none;
    border-radius: 0.4rem;
  }
  button:disabled {
    opacity: 0.6;
    cursor: default;
  }
  .cta {
    display: block;
    text-align: center;
    padding: 0.6rem;
    margin: 1rem 0 0.75rem;
    background: #2b6cb0;
    color: #fff;
    border-radius: 0.4rem;
    text-decoration: none;
    font-weight: 600;
  }
  .cta.secondary {
    margin-top: 0;
    background: #17233c;
    color: #e8ecf5;
    border: 1px solid #26314a;
  }
  .muted {
    color: #8a93a6;
    font-size: 0.85rem;
  }
  .error {
    color: #ff8f8f;
    font-size: 0.9rem;
  }
  .version {
    color: #8a93a6;
    font-size: 0.8rem;
    margin-top: 2rem;
  }
</style>
