<script lang="ts">
  // First-boot wizard + sign-in. Once authed, the Live view is one tap away.
  import { onMount } from 'svelte';
  import { api, detail, fetchPulseoxStatus, fetchSession, fetchStatus, type User } from '$lib/api';

  type LoginResult = { totp_required: boolean; challenge: string | null; user: User | null };
  type View = 'loading' | 'first-boot' | 'login' | 'totp' | 'authed';

  const MIN_PASSWORD = 12;

  let view = $state<View>('loading');
  let version = $state('');
  let user = $state<User | null>(null);
  let username = $state('');
  let password = $state('');
  let remember = $state(true); // "remember me" — a persistent session, on by default
  let confirm = $state('');
  let challenge = $state('');
  let totpCode = $state('');
  let error = $state('');
  let busy = $state(false);
  // Pulse-ox is optional; only surface its nav link where the profile is enabled.
  let pulseoxProfile = $state(false);
  // Grandparent mode: a viewer's home is scoped to Live + Tonight; the management
  // surfaces (Trends, Devices, Pulse-ox, Settings) are admin-only.
  const isAdmin = $derived(user?.role === 'admin');

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

  // Whichever path reaches the authed view (mount, login, first-boot, TOTP), discover
  // whether the optional pulse-ox profile is on so the nav link can appear.
  $effect(() => {
    if (view !== 'authed') return;
    void (async () => {
      try {
        pulseoxProfile = (await fetchPulseoxStatus()).profile_enabled;
      } catch {
        pulseoxProfile = false;
      }
    })();
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
        body: JSON.stringify({ username, password, remember }),
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
        body: JSON.stringify({ challenge, code: totpCode, remember }),
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

<main class="home container">
  <header class="brand">
    <span class="mark" aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
        <path d="M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z" />
      </svg>
    </span>
    <div>
      <h1>eeper</h1>
      <p class="tagline muted">Your little one's night, at a glance.</p>
    </div>
  </header>

  {#if view === 'loading'}
    <div class="card"><p class="muted">Loading…</p></div>
  {:else if view === 'first-boot'}
    <section class="card auth">
      <h2>Create your admin account</h2>
      <p class="muted">First-time setup. There are no default credentials.</p>
      <form onsubmit={submitFirstBoot} class="stack">
        <label
          >Username<input
            class="input"
            bind:value={username}
            autocomplete="username"
            required
            minlength="3"
          /></label
        >
        <label
          >Password<input
            class="input"
            type="password"
            bind:value={password}
            autocomplete="new-password"
            required
            minlength={MIN_PASSWORD}
          /></label
        >
        <label
          >Confirm password<input
            class="input"
            type="password"
            bind:value={confirm}
            autocomplete="new-password"
            required
          /></label
        >
        <button class="btn btn--primary btn--block" type="submit" disabled={busy}
          >{busy ? 'Creating…' : 'Create admin'}</button
        >
      </form>
    </section>
  {:else if view === 'login'}
    <section class="card auth">
      <!-- The heading is a stable "Sign in" (the e2e sign-in guard selects on it);
           "Welcome back" is the visible display title. -->
      <h2 class="visually-hidden">Sign in</h2>
      <p class="auth-title">Welcome back</p>
      <p class="muted">Sign in to check on tonight.</p>
      <form onsubmit={submitLogin} class="stack">
        <label
          >Username<input
            class="input"
            bind:value={username}
            autocomplete="username"
            required
          /></label
        >
        <label
          >Password<input
            class="input"
            type="password"
            bind:value={password}
            autocomplete="current-password"
            required
          /></label
        >
        <label class="remember">
          <input type="checkbox" bind:checked={remember} data-testid="remember-me" />
          <span>Keep me signed in</span>
        </label>
        <button class="btn btn--primary btn--block" type="submit" disabled={busy}
          >{busy ? 'Signing in…' : 'Sign in'}</button
        >
      </form>
    </section>
  {:else if view === 'totp'}
    <section class="card auth">
      <h2>Two-factor code</h2>
      <p class="muted">Enter the 6-digit code from your authenticator app.</p>
      <form onsubmit={submitTotp} class="stack">
        <label
          >Code<input
            class="input"
            bind:value={totpCode}
            inputmode="numeric"
            autocomplete="one-time-code"
            required
          /></label
        >
        <button class="btn btn--primary btn--block" type="submit" disabled={busy}
          >{busy ? 'Verifying…' : 'Verify'}</button
        >
      </form>
    </section>
  {:else if view === 'authed' && user}
    <section class="authed stack">
      <div class="welcome">
        <span class="pill pill--ok">Signed in</span>
        <p class="muted">as <strong>{user.username}</strong> · {user.role}</p>
      </div>
      <h2 class="visually-hidden">Signed in</h2>

      <a class="hero-cta" href="/live">
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="1.8"
          aria-hidden="true"
        >
          <path d="M23 7l-7 5 7 5V7z" /><rect x="1" y="5" width="15" height="14" rx="3" />
        </svg>
        <span>Open live view</span>
      </a>

      <nav class="tiles" aria-label="Sections">
        <a class="tile" href="/tonight">
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.8"
            aria-hidden="true"><path d="M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z" /></svg
          ><span>Tonight</span>
        </a>
        {#if isAdmin}
          <a class="tile" href="/trends">
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linecap="round"
              aria-hidden="true"><path d="M3 3v18h18" /><path d="M7 14l4-4 3 3 5-6" /></svg
            ><span>Trends</span>
          </a>
          <a class="tile" href="/devices">
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.8"
              aria-hidden="true"
              ><rect x="4" y="4" width="16" height="16" rx="3" /><path d="M9 9h6v6H9z" /></svg
            ><span>Devices</span>
          </a>
          {#if pulseoxProfile}
            <a class="tile" href="/pulseox">
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="1.8"
                stroke-linecap="round"
                aria-hidden="true"><path d="M3 12h4l2 6 4-14 2 8h6" /></svg
              ><span>Pulse-ox</span>
            </a>
          {/if}
          <a class="tile" href="/settings">
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.8"
              aria-hidden="true"
              ><circle cx="12" cy="12" r="3" /><path
                d="M19 12a7 7 0 00-.1-1.3l2-1.6-2-3.4-2.4 1a7 7 0 00-2.3-1.3L14 2h-4l-.3 2.4a7 7 0 00-2.3 1.3l-2.4-1-2 3.4 2 1.6A7 7 0 005 12a7 7 0 00.1 1.3l-2 1.6 2 3.4 2.4-1a7 7 0 002.3 1.3L10 22h4l.3-2.4a7 7 0 002.3-1.3l2.4 1 2-3.4-2-1.6A7 7 0 0019 12z"
              /></svg
            ><span>Settings</span>
          </a>
        {/if}
      </nav>

      <button class="btn btn--ghost btn--block" type="button" onclick={logout}>Sign out</button>
    </section>
  {/if}

  {#if error}<p class="error" role="alert">{error}</p>{/if}
  {#if version}<p class="version muted">v{version}</p>{/if}
</main>

<style>
  .home {
    padding-top: max(var(--sp-6), env(safe-area-inset-top));
    display: flex;
    flex-direction: column;
    gap: var(--sp-5);
  }
  .brand {
    display: flex;
    align-items: center;
    gap: var(--sp-4);
  }
  .mark {
    display: grid;
    place-items: center;
    width: 52px;
    height: 52px;
    border-radius: var(--r);
    background: var(--accent-subtle);
    color: var(--accent);
    box-shadow: inset 0 0 0 1px var(--border);
  }
  .mark svg {
    width: 26px;
    height: 26px;
  }
  h1 {
    margin: 0;
  }
  .tagline {
    margin: 2px 0 0;
    font-size: var(--fs-sm);
  }

  .auth h2 {
    margin-bottom: var(--sp-1);
  }
  .auth .muted {
    margin: 0 0 var(--sp-4);
    font-size: var(--fs-sm);
  }
  .auth form {
    margin-top: var(--sp-2);
  }

  .visually-hidden {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
    white-space: nowrap;
  }
  .auth-title {
    margin: 0;
    font-size: var(--fs-lg);
    font-weight: 700;
    letter-spacing: -0.01em;
    color: var(--text);
  }
  .remember {
    flex-direction: row;
    align-items: center;
    gap: var(--sp-2);
    font-size: var(--fs-sm);
    color: var(--text-2);
    cursor: pointer;
    user-select: none;
  }
  .remember input {
    width: auto;
    min-height: 0;
    accent-color: var(--accent);
  }
  .welcome {
    display: flex;
    align-items: center;
    gap: var(--sp-3);
  }
  .welcome p {
    margin: 0;
    font-size: var(--fs-sm);
  }

  .hero-cta {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--sp-3);
    min-height: 72px;
    border-radius: var(--r-lg);
    background: linear-gradient(135deg, var(--accent), var(--accent-strong));
    color: var(--accent-ink);
    font-size: var(--fs-lg);
    font-weight: 750;
    box-shadow: var(--shadow);
    transition: transform 0.06s ease;
  }
  .hero-cta:active {
    transform: translateY(1px) scale(0.995);
  }
  .hero-cta svg {
    width: 26px;
    height: 26px;
  }

  .tiles {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: var(--sp-3);
  }
  .tile {
    display: flex;
    flex-direction: column;
    gap: var(--sp-2);
    padding: var(--sp-4);
    min-height: 92px;
    border-radius: var(--r);
    background: var(--surface);
    border: 1px solid var(--border);
    color: var(--text);
    font-weight: 650;
    box-shadow: var(--shadow-sm);
    transition:
      transform 0.06s ease,
      border-color 0.15s ease;
  }
  .tile:active {
    transform: translateY(1px);
  }
  .tile:hover {
    border-color: var(--border-hi);
  }
  .tile svg {
    width: 24px;
    height: 24px;
    color: var(--accent);
  }

  .error {
    margin: 0;
    padding: var(--sp-3) var(--sp-4);
    border-radius: var(--r-sm);
    background: var(--danger-subtle);
    color: var(--danger);
    font-size: var(--fs-sm);
  }
  .version {
    text-align: center;
    font-size: var(--fs-xs);
  }
</style>
