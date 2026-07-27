<script lang="ts">
  // Settings (M4.3): an admin-only hub consolidating the account + the management
  // surfaces. Viewers ("grandparent mode") are scoped to Live + Tonight and are
  // redirected away from here. Notification preferences live on the Tonight view (so a
  // viewer can still manage their own), and this page links to them.
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import {
    changePassword,
    fetchPulseoxStatus,
    fetchSession,
    fetchStatus,
    type User,
    fetchRecordingSettings,
    fetchStorageTargets,
    updateRecordingSettings,
    type RecordingSettings,
    type StorageTarget,
    type StorageTargets,
  } from '$lib/api';
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
  // Recording controls. `null` means we couldn't load them (older API, or lite mode, which
  // has no recorder) — the card stays hidden rather than showing a toggle that does nothing.
  let recording = $state<RecordingSettings | null>(null);
  let recSaving = $state(false);
  let recErr = $state('');

  // Where recordings go. The list is whatever the operator declared on the host (eeper
  // can't discover or mount disks itself), re-probed on each load for free space and
  // whether the disk is actually there.
  let storage = $state<StorageTargets | null>(null);
  let savingTarget = $state('');

  async function toggleRecording(enabled: boolean): Promise<void> {
    recErr = '';
    recSaving = true;
    try {
      recording = await updateRecordingSettings({ recording_enabled: enabled });
    } catch (e) {
      recErr = e instanceof Error ? e.message : 'Could not save the setting.';
    } finally {
      recSaving = false;
    }
  }

  async function selectTarget(id: string): Promise<void> {
    if (!storage || storage.selected_id === id) return;
    recErr = '';
    savingTarget = id;
    try {
      recording = await updateRecordingSettings({ storage_target_id: id });
      // Re-probe: the free space that matters now is the new disk's.
      storage = await fetchStorageTargets();
    } catch (e) {
      recErr = e instanceof Error ? e.message : 'Could not change where clips are saved.';
    } finally {
      savingTarget = '';
    }
  }

  function fmtBytes(n?: number | null): string {
    if (n == null) return '—';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let v = n;
    let i = 0;
    while (v >= 1024 && i < units.length - 1) {
      v /= 1024;
      i++;
    }
    return `${v < 10 && i > 0 ? v.toFixed(1) : Math.round(v)} ${units[i]}`;
  }

  /** What to say under a target's name — free space when it's usable, why not when it
   * isn't. A declared target that exists but isn't a mount point is the subtle one: writes
   * succeed, they just land on the internal card instead of the disk. */
  function targetNote(t: StorageTarget): string {
    if (t.error === 'not_mounted') return 'Not connected';
    if (t.error === 'not_writable') return 'Connected, but eeper cannot write to it';
    if (t.error) return 'Could not be read';
    if (!t.mounted) return `${fmtBytes(t.free_bytes)} free — warning: no disk mounted here`;
    return `${fmtBytes(t.free_bytes)} free of ${fmtBytes(t.total_bytes)}`;
  }

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

  // ── change password ──
  const MIN_PW = 12; // mirrors the server's min_password_length
  let curPw = $state('');
  let newPw = $state('');
  let confirmPw = $state('');
  let pwErr = $state('');
  let pwMsg = $state('');
  let pwBusy = $state(false);

  async function submitPassword(e: SubmitEvent): Promise<void> {
    e.preventDefault();
    pwErr = '';
    pwMsg = '';
    if (newPw.length < MIN_PW) {
      pwErr = `New password must be at least ${MIN_PW} characters.`;
      return;
    }
    if (newPw !== confirmPw) {
      pwErr = 'The new passwords do not match.';
      return;
    }
    if (newPw === curPw) {
      pwErr = 'New password must be different from the current one.';
      return;
    }
    pwBusy = true;
    try {
      await changePassword(curPw, newPw);
      pwMsg = 'Password changed.';
      curPw = '';
      newPw = '';
      confirmPw = '';
    } catch (err) {
      pwErr = err instanceof Error ? err.message : 'Could not change the password.';
    } finally {
      pwBusy = false;
    }
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
        recording = await fetchRecordingSettings().catch(() => null);
        storage = await fetchStorageTargets().catch(() => null);
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

      <form class="pw-form" onsubmit={submitPassword} data-testid="change-password">
        <h3>Change password</h3>
        <label>
          Current password
          <input
            class="input"
            type="password"
            autocomplete="current-password"
            bind:value={curPw}
            data-testid="cp-current"
            required
          />
        </label>
        <label>
          New password
          <input
            class="input"
            type="password"
            autocomplete="new-password"
            bind:value={newPw}
            data-testid="cp-new"
            minlength={MIN_PW}
            required
          />
        </label>
        <label>
          Confirm new password
          <input
            class="input"
            type="password"
            autocomplete="new-password"
            bind:value={confirmPw}
            data-testid="cp-confirm"
            required
          />
        </label>
        <p class="pw-hint">At least {MIN_PW} characters. Signs out your other browser sessions.</p>
        {#if pwErr}<p class="pw-err" role="alert" data-testid="cp-error">{pwErr}</p>{/if}
        {#if pwMsg}<p class="pw-ok" role="status" data-testid="cp-success">{pwMsg}</p>{/if}
        <button
          type="submit"
          class="btn btn--primary btn--block"
          data-testid="cp-submit"
          disabled={pwBusy}
        >
          {pwBusy ? 'Changing…' : 'Change password'}
        </button>
      </form>
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

    {#if recording}
      <section class="card" data-testid="settings-recording">
        <h2>Recording</h2>
        <label class="row">
          <input
            type="checkbox"
            data-testid="recording-toggle"
            checked={recording.recording_enabled}
            disabled={recSaving}
            onchange={(e) => void toggleRecording(e.currentTarget.checked)}
          />
          <span>Record clips</span>
        </label>
        <p class="hint">
          Saves short video around detected sounds so you can play them back in Tonight. When this
          is off, nothing is written to disk and Tonight shows those moments without a clip.
        </p>

        {#if storage && storage.targets.length > 1}
          <!-- Only worth showing when there is an actual choice: with one target this is a
               picker with nothing to pick. -->
          <div class="sub" data-testid="storage-targets">
            <h3>Where clips are saved</h3>
            {#each storage.targets as t (t.id)}
              <label class="target" class:target--bad={!t.writable}>
                <input
                  type="radio"
                  name="storage-target"
                  value={t.id}
                  checked={storage.selected_id === t.id}
                  disabled={!t.writable || savingTarget !== ''}
                  onchange={() => void selectTarget(t.id)}
                  data-testid="storage-target-{t.id}"
                />
                <span class="target__text">
                  <span class="target__label">{t.label}</span>
                  <span class="target__note">{targetNote(t)}</span>
                </span>
              </label>
            {/each}
            <p class="hint hint--tight">
              Recordings and saved clips move together — new ones go to the disk you pick here.
              Anything already recorded stays where it is and still plays back.
            </p>
          </div>
        {/if}

        {#if recErr}<p class="pw-err" role="alert" data-testid="recording-error">{recErr}</p>{/if}
      </section>
    {/if}

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
  .pw-form {
    display: flex;
    flex-direction: column;
    gap: var(--sp-3);
    margin-top: var(--sp-4);
    padding-top: var(--sp-4);
    border-top: 1px solid var(--border);
  }
  .pw-form h3 {
    margin: 0;
    font-size: var(--fs-base);
    font-weight: 700;
  }
  .pw-hint {
    margin: 0;
    color: var(--text-muted);
    font-size: var(--fs-xs);
  }
  .pw-err {
    margin: 0;
    color: var(--danger);
    font-size: var(--fs-sm);
  }
  .pw-ok {
    margin: 0;
    color: var(--ok);
    font-size: var(--fs-sm);
  }
  /* ── storage target picker ── */
  .sub {
    margin-top: var(--sp-4);
    padding-top: var(--sp-4);
    border-top: 1px solid var(--border);
  }
  .sub h3 {
    margin: 0 0 var(--sp-2);
    font-size: var(--fs-base);
    font-weight: 700;
  }
  .target {
    display: flex;
    align-items: center;
    gap: var(--sp-3);
    padding: var(--sp-2) 0;
    min-height: var(--tap);
  }
  .target__text {
    display: flex;
    flex-direction: column;
  }
  .target__label {
    font-size: var(--fs-base);
  }
  .target__note {
    color: var(--text-muted);
    font-size: var(--fs-xs);
  }
  .target--bad .target__note {
    color: var(--danger);
  }
  .hint--tight {
    margin: var(--sp-2) 0 0;
    font-size: var(--fs-xs);
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
