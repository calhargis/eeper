<script lang="ts">
  // Settings (M4.3): an admin-only hub consolidating the account + the management
  // surfaces. Viewers ("grandparent mode") are scoped to Live + Tonight and are
  // redirected away from here. Notification preferences live on the Tonight view (so a
  // viewer can still manage their own), and this page links to them.
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { fetchPulseoxStatus, fetchSession, fetchStatus, type User } from '$lib/api';
  import {
    CATEGORIES,
    PRESETS,
    applyTheme,
    clearSaved,
    clearTheme,
    customTheme,
    hslToHex,
    loadTheme,
    saveTheme,
    toHslMap,
    type Category,
    type Hsl,
    type Theme,
  } from '$lib/theme';

  let ready = $state(false);
  let user = $state<User | null>(null);
  let version = $state('');
  let pulseoxProfile = $state(false);

  // ── appearance / theming ──
  // activeId is the selected preset id, 'custom', or 'system' (follow the OS).
  let activeId = $state<string>('system');
  let openCat = $state<Category | null>(null);
  // Slider state for the custom editor (Hue/Sat/Light per category), seeded on demand.
  let hsl = $state<Record<Category, Hsl>>(toHslMap(PRESETS[0].vars));

  function choosePreset(p: Theme): void {
    applyTheme(p);
    saveTheme(p);
    activeId = p.id;
    openCat = null;
  }

  function startCustom(): void {
    // Seed the sliders from wherever we are now, so tweaking starts from something calm.
    const saved = loadTheme();
    const seed =
      saved?.id === 'custom'
        ? saved.vars
        : (PRESETS.find((p) => p.id === activeId)?.vars ?? PRESETS[0].vars);
    hsl = toHslMap(seed);
    activeId = 'custom';
    openCat = 'accent';
  }

  function toggleCat(k: Category): void {
    openCat = openCat === k ? null : k;
  }

  function resetTheme(): void {
    clearTheme();
    clearSaved();
    activeId = 'system';
    openCat = null;
  }

  // Live-apply while the custom sliders move (deeply tracks hsl + activeId).
  $effect(() => {
    if (activeId !== 'custom') return;
    const t = customTheme(hsl);
    applyTheme(t);
    saveTheme(t);
  });

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
      // Reflect the persisted theme in the picker (the head-script already painted it).
      const saved = loadTheme();
      if (saved) {
        activeId = saved.id;
        if (saved.id === 'custom') hsl = toHslMap(saved.vars);
      }
      ready = true;
    })();
  });
</script>

<svelte:head><title>eeper — settings</title></svelte:head>

{#if !ready}
  <p class="loading">Loading…</p>
{:else}
  <header class="appbar">
    <a href="/" class="back" aria-label="Back">‹</a>
    <span class="title">Settings</span>
    <span class="spacer"></span>
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

    <section class="card" data-testid="settings-appearance">
      <h2>Appearance</h2>
      <p class="hint">Choose a theme, or build your own with the sliders.</p>

      <div class="presets">
        {#each PRESETS as p (p.id)}
          <button
            type="button"
            class="preset"
            class:active={activeId === p.id}
            data-testid={`theme-${p.id}`}
            aria-pressed={activeId === p.id}
            onclick={() => choosePreset(p)}
          >
            <span class="chip" style="background:{p.vars.bg}">
              <span class="chip-card" style="background:{p.vars.surface}"></span>
              <span class="chip-dot" style="background:{p.vars.accent}"></span>
            </span>
            <span class="pname">{p.name}</span>
          </button>
        {/each}
        <button
          type="button"
          class="preset"
          class:active={activeId === 'custom'}
          data-testid="theme-custom"
          aria-pressed={activeId === 'custom'}
          onclick={startCustom}
        >
          <span class="chip chip-rainbow"></span>
          <span class="pname">Custom</span>
        </button>
      </div>

      {#if activeId === 'custom'}
        <div class="custom" data-testid="theme-custom-editor">
          {#each CATEGORIES as c (c.key)}
            <div class="cat" class:open={openCat === c.key}>
              <button
                type="button"
                class="cat-head"
                aria-expanded={openCat === c.key}
                onclick={() => toggleCat(c.key)}
              >
                <span class="cat-sw" style="background:{hslToHex(hsl[c.key])}"></span>
                <span class="cat-text">
                  <span class="cat-name">{c.label}</span>
                  <span class="cat-hint">{c.hint}</span>
                </span>
                <span class="chev" aria-hidden="true">{openCat === c.key ? '▾' : '▸'}</span>
              </button>
              {#if openCat === c.key}
                <div class="sliders">
                  <label class="sld">
                    <span class="sld-k">Hue</span>
                    <input
                      type="range"
                      min="0"
                      max="360"
                      bind:value={hsl[c.key].h}
                      data-testid={`slider-${c.key}-h`}
                    />
                    <output>{hsl[c.key].h}°</output>
                  </label>
                  <label class="sld">
                    <span class="sld-k">Saturation</span>
                    <input
                      type="range"
                      min="0"
                      max="100"
                      bind:value={hsl[c.key].s}
                      data-testid={`slider-${c.key}-s`}
                    />
                    <output>{hsl[c.key].s}%</output>
                  </label>
                  <label class="sld">
                    <span class="sld-k">Lightness</span>
                    <input
                      type="range"
                      min="0"
                      max="100"
                      bind:value={hsl[c.key].l}
                      data-testid={`slider-${c.key}-l`}
                    />
                    <output>{hsl[c.key].l}%</output>
                  </label>
                </div>
              {/if}
            </div>
          {/each}
        </div>
      {/if}

      <button
        type="button"
        class="btn btn--ghost btn--block reset"
        data-testid="theme-reset"
        onclick={resetTheme}
      >
        {activeId === 'system' ? 'Following system theme' : 'Reset to system default'}
      </button>
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
  .who {
    color: var(--text-muted);
    font-size: var(--fs-sm);
  }
  .loading {
    text-align: center;
    margin: var(--sp-7) var(--sp-4);
    color: var(--text-muted);
  }
  main {
    max-width: var(--maxw);
    margin: var(--sp-4) auto;
    padding: 0 var(--sp-4);
  }
  .card {
    margin-bottom: var(--sp-4);
  }
  .card h2 {
    font-size: var(--fs-sm);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    margin: 0 0 var(--sp-3);
    color: var(--text-muted);
  }
  .row {
    display: flex;
    justify-content: space-between;
    padding: var(--sp-2) 0;
    font-size: var(--fs-base);
  }
  .row .k {
    color: var(--text-muted);
  }
  .link {
    display: block;
    padding: var(--sp-3) 0;
    color: var(--accent);
    text-decoration: none;
    border-top: 1px solid var(--border);
    min-height: var(--tap);
  }
  .link:first-of-type {
    border-top: none;
  }
  .version {
    text-align: center;
    color: var(--text-muted);
    font-size: var(--fs-xs);
  }

  /* ── appearance / theme picker ── */
  .hint {
    margin: 0 0 var(--sp-3);
    color: var(--text-muted);
    font-size: var(--fs-sm);
  }
  .presets {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(96px, 1fr));
    gap: var(--sp-3);
  }
  .preset {
    display: flex;
    flex-direction: column;
    align-items: stretch;
    gap: var(--sp-2);
    padding: var(--sp-2);
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: var(--r-sm);
    color: var(--text-2);
    font: inherit;
    font-size: var(--fs-xs);
    font-weight: 650;
    cursor: pointer;
    transition:
      transform 0.06s ease,
      border-color 0.15s ease;
  }
  .preset:active {
    transform: translateY(1px) scale(0.99);
  }
  .preset.active {
    border-color: var(--accent);
    box-shadow: var(--ring);
  }
  .pname {
    text-align: center;
  }
  .chip {
    position: relative;
    display: block;
    height: 44px;
    border-radius: var(--r-sm);
    border: 1px solid rgba(128, 128, 128, 0.25);
    overflow: hidden;
  }
  .chip-card {
    position: absolute;
    left: 8px;
    right: 8px;
    bottom: 7px;
    height: 15px;
    border-radius: 5px;
  }
  .chip-dot {
    position: absolute;
    top: 7px;
    right: 8px;
    width: 12px;
    height: 12px;
    border-radius: 50%;
  }
  .chip-rainbow {
    background: conic-gradient(
      from 210deg,
      #f28b7a,
      #f0b24a,
      #7dd6a4,
      #6fd6c4,
      #7c8cf8,
      #f2879f,
      #f28b7a
    );
  }

  .custom {
    margin-top: var(--sp-4);
    display: flex;
    flex-direction: column;
    gap: var(--sp-2);
  }
  .cat {
    border: 1px solid var(--border);
    border-radius: var(--r-sm);
    overflow: hidden;
  }
  .cat.open {
    border-color: var(--border-hi);
  }
  .cat-head {
    width: 100%;
    display: flex;
    align-items: center;
    gap: var(--sp-3);
    min-height: var(--tap);
    padding: var(--sp-2) var(--sp-3);
    background: var(--surface-2);
    border: none;
    color: var(--text);
    font: inherit;
    text-align: left;
    cursor: pointer;
  }
  .cat-sw {
    width: 24px;
    height: 24px;
    flex: none;
    border-radius: 6px;
    border: 1px solid rgba(128, 128, 128, 0.3);
  }
  .cat-text {
    display: flex;
    flex-direction: column;
    line-height: 1.25;
  }
  .cat-name {
    font-weight: 650;
  }
  .cat-hint {
    color: var(--text-muted);
    font-size: var(--fs-xs);
  }
  .chev {
    margin-left: auto;
    color: var(--text-muted);
  }
  .sliders {
    display: flex;
    flex-direction: column;
    gap: var(--sp-3);
    padding: var(--sp-3);
    background: var(--surface);
  }
  .sld {
    display: grid;
    grid-template-columns: 5.5rem 1fr 3rem;
    align-items: center;
    gap: var(--sp-3);
    font-size: var(--fs-sm);
    color: var(--text-2);
  }
  .sld-k {
    white-space: nowrap;
  }
  .sld input[type='range'] {
    width: 100%;
    accent-color: var(--accent);
  }
  .sld output {
    text-align: right;
    color: var(--text-muted);
    font-variant-numeric: tabular-nums;
  }
  .reset {
    margin-top: var(--sp-4);
  }
</style>
