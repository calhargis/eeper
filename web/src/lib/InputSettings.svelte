<script lang="ts">
  // Per-input settings: a round button UNDER the picture, opening a sheet that rises from the
  // bottom without covering the stage.
  //
  // The previous attempt put these controls in the stage's own bottom bar, next to fullscreen,
  // behind a `⤢` glyph — which is the standard expand icon. It read as a second fullscreen
  // button that did nothing. Two lessons baked in here: the control is not on the picture, and
  // it does not borrow an icon that already means something else.
  //
  // One component for every input kind. A thermal node is mounted over a crib exactly like a
  // camera, so "which way up is it" is the same question and deserves the same answer.
  import {
    IDENTITY,
    describe,
    isIdentity,
    loadTransform,
    nextRotation,
    saveTransform,
    type CameraTransform,
  } from '$lib/camera-transform';

  let { storageKey, title }: { storageKey: string; title: string } = $props();

  let open = $state(false);
  let version = $state(0);
  const current: CameraTransform = $derived.by(() => {
    void version;
    return loadTransform(storageKey);
  });

  function apply(next: CameraTransform): void {
    saveTransform(storageKey, next);
    version += 1;
    // The stage above re-reads on this event, so the picture turns while the sheet stays put.
    window.dispatchEvent(new CustomEvent('camera-transform', { detail: { storageKey } }));
  }
</script>

<div class="bar">
  <button
    type="button"
    class="trigger"
    class:trigger--on={open}
    aria-expanded={open}
    aria-label="{title} settings"
    title="{title} settings"
    data-testid="input-settings-toggle"
    onclick={() => (open = !open)}
  >
    <!-- A gear, not an expand glyph: this must not read as another fullscreen button. -->
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
      <circle cx="12" cy="12" r="3" />
      <path
        d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"
      />
    </svg>
  </button>
  {#if !isIdentity(current)}
    <span class="state" data-testid="input-settings-state">{describe(current)}</span>
  {/if}
</div>

{#if open}
  <!-- Below the stage, in normal flow: it pushes the page rather than covering the picture,
       so you can watch the effect of every press while the sheet is open. -->
  <section class="sheet" data-testid="input-settings-panel" aria-label="{title} settings">
    <header class="sheet__head">
      <h2>{title}</h2>
      <button
        type="button"
        class="sheet__close"
        aria-label="Close settings"
        data-testid="input-settings-close"
        onclick={() => (open = false)}>✕</button
      >
    </header>

    <h3 class="sheet__group">Orientation</h3>
    <div class="row">
      <button
        type="button"
        class="btn btn--ghost"
        class:on={current.flipH}
        aria-pressed={current.flipH}
        data-testid="orient-flip-h"
        onclick={() => apply({ ...current, flipH: !current.flipH })}>↔ Mirror</button
      >
      <button
        type="button"
        class="btn btn--ghost"
        class:on={current.flipV}
        aria-pressed={current.flipV}
        data-testid="orient-flip-v"
        onclick={() => apply({ ...current, flipV: !current.flipV })}>↕ Flip</button
      >
      <button
        type="button"
        class="btn btn--ghost"
        data-testid="orient-rotate"
        onclick={() => apply({ ...current, rotation: nextRotation(current.rotation) })}
        >⟳ Rotate</button
      >
      <button
        type="button"
        class="btn btn--ghost"
        disabled={isIdentity(current)}
        data-testid="orient-reset"
        onclick={() => apply({ ...IDENTITY })}>Reset</button
      >
    </div>
    <p class="note">
      Changes how the picture is shown on this device. Recordings keep the camera's own orientation.
    </p>
  </section>
{/if}

<style>
  .bar {
    display: flex;
    align-items: center;
    gap: var(--sp-3);
    padding: var(--sp-2) 0;
  }
  .trigger {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: var(--tap);
    height: var(--tap);
    padding: 0;
    border: 1px solid var(--border);
    border-radius: 50%;
    background: var(--surface-2);
    color: var(--text-2);
    cursor: pointer;
  }
  .trigger--on {
    border-color: var(--accent);
    color: var(--accent);
  }
  .trigger svg {
    width: 20px;
    height: 20px;
  }
  .state {
    color: var(--text-muted);
    font-size: var(--fs-xs);
  }
  .sheet {
    margin-top: var(--sp-2);
    padding: var(--sp-3);
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: var(--r);
    animation: rise 0.16s ease-out;
  }
  @keyframes rise {
    from {
      opacity: 0;
      transform: translateY(8px);
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .sheet {
      animation: none;
    }
  }
  .sheet__head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--sp-3);
  }
  .sheet__head h2 {
    margin: 0;
    font-size: var(--fs-sm);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--text-muted);
  }
  .sheet__close {
    min-width: var(--tap);
    min-height: var(--tap);
    border: none;
    background: none;
    color: var(--text-muted);
    font-size: var(--fs-base);
    cursor: pointer;
  }
  .sheet__group {
    margin: var(--sp-3) 0 var(--sp-2);
    font-size: var(--fs-sm);
    font-weight: 700;
  }
  .row {
    display: flex;
    flex-wrap: wrap;
    gap: var(--sp-2);
  }
  .row .btn {
    flex: 1 1 auto;
    min-width: 6rem;
  }
  .row .btn.on {
    border-color: var(--accent);
    color: var(--accent);
  }
  .note {
    margin: var(--sp-3) 0 0;
    color: var(--text-muted);
    font-size: var(--fs-xs);
  }
</style>
