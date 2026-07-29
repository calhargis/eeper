<script lang="ts">
  // Mirror / flip / rotate, on the stage itself.
  //
  // It lives here rather than in Settings because the picture IS the preview: you press a
  // button and watch the thing you are correcting. Sending someone to a settings page to fix
  // a sideways camera means adjusting one screen while looking at another.
  //
  // Collapsed to a single button by default — a live view should be mostly picture, and
  // orientation is something you set once when the camera is mounted and then forget.
  import {
    IDENTITY,
    describe,
    isIdentity,
    loadTransform,
    nextRotation,
    saveTransform,
    type CameraTransform,
  } from '$lib/camera-transform';

  let { storageKey, label = 'Orientation' }: { storageKey: string; label?: string } = $props();

  let open = $state(false);
  let version = $state(0);
  const current: CameraTransform = $derived.by(() => {
    void version; // re-read when this component (or another) changes it
    return loadTransform(storageKey);
  });

  function apply(next: CameraTransform): void {
    saveTransform(storageKey, next);
    version += 1;
    // The stage this sits on re-reads on the same event, so the picture turns underneath the
    // buttons as they are pressed.
    window.dispatchEvent(new CustomEvent('camera-transform', { detail: { storageKey } }));
  }
</script>

<div class="orient" class:orient--open={open}>
  {#if open}
    <button
      type="button"
      class="ctl orient__btn"
      class:on={current.flipH}
      aria-pressed={current.flipH}
      data-testid="orient-flip-h"
      title="Mirror horizontally"
      onclick={() => apply({ ...current, flipH: !current.flipH })}>↔</button
    >
    <button
      type="button"
      class="ctl orient__btn"
      class:on={current.flipV}
      aria-pressed={current.flipV}
      data-testid="orient-flip-v"
      title="Flip vertically"
      onclick={() => apply({ ...current, flipV: !current.flipV })}>↕</button
    >
    <button
      type="button"
      class="ctl orient__btn"
      data-testid="orient-rotate"
      title="Rotate 90°"
      onclick={() => apply({ ...current, rotation: nextRotation(current.rotation) })}>⟳</button
    >
    <button
      type="button"
      class="ctl orient__btn orient__btn--wide"
      disabled={isIdentity(current)}
      data-testid="orient-reset"
      title="Reset orientation"
      onclick={() => apply({ ...IDENTITY })}>Reset</button
    >
  {/if}
  <button
    type="button"
    class="ctl orient__btn"
    class:on={open || !isIdentity(current)}
    aria-expanded={open}
    aria-label="{label} — {describe(current)}"
    title="{label} — {describe(current)}"
    data-testid="orient-toggle"
    onclick={() => (open = !open)}>⤢</button
  >
</div>

<style>
  .orient {
    display: flex;
    align-items: center;
    gap: var(--sp-1);
  }
  .orient__btn {
    width: var(--tap);
    padding: 0;
    font-size: var(--fs-base);
    line-height: 1;
  }
  .orient__btn--wide {
    width: auto;
    padding: 0 var(--sp-3);
    font-size: var(--fs-sm);
  }
  .orient__btn.on {
    background: var(--accent);
    color: var(--accent-ink);
  }
  .orient__btn:disabled {
    opacity: 0.45;
    cursor: default;
  }
  /* Landscape on a phone: the row of controls must not crowd out the picture. */
  @media (max-height: 480px) {
    .orient__btn {
      width: 2.25rem;
      min-height: 2.25rem;
    }
  }
</style>
