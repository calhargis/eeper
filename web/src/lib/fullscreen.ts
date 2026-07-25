// Fullscreen for the live views (camera + thermal), with one behaviour across browsers.
//
// The Element Fullscreen API is NOT available on iPhone Safari — only iPad/desktop have it
// (iPhone exposes fullscreen for <video> alone, which would hide our own overlay controls
// and can't show a <canvas> at all). So we pick per-element: use the real API when the
// browser has it, otherwise fall back to a CSS "faux" fullscreen — a fixed, viewport-filling
// stage. Installed as a home-screen PWA (the common case here) there is no browser chrome,
// so the faux mode looks identical to true fullscreen.

/** How a given element should go fullscreen in this browser. */
export type FullscreenStrategy = 'native' | 'faux';

/** Minimal shape we need — kept structural so the choice is unit-testable without a DOM. */
export type FullscreenCapableElement = {
  requestFullscreen?: unknown;
  webkitRequestFullscreen?: unknown;
};
export type FullscreenCapableDocument = {
  fullscreenEnabled?: boolean;
  webkitFullscreenEnabled?: boolean;
};

/**
 * Decide how to go fullscreen. Native needs BOTH a request method on the element and the
 * document reporting fullscreen as enabled — iPhone Safari fails the second check (and
 * often the first), and an iframe without `allowfullscreen` fails it too. Anything short of
 * that gets the CSS fallback, which always works.
 */
export function pickFullscreenStrategy(
  el: FullscreenCapableElement | null | undefined,
  doc: FullscreenCapableDocument | null | undefined,
): FullscreenStrategy {
  if (!el || !doc) return 'faux';
  const hasRequest =
    typeof el.requestFullscreen === 'function' || typeof el.webkitRequestFullscreen === 'function';
  const enabled = doc.fullscreenEnabled === true || doc.webkitFullscreenEnabled === true;
  return hasRequest && enabled ? 'native' : 'faux';
}

/** True when `el` (or any element) is currently the browser's native fullscreen element. */
export function isNativeFullscreen(el?: Element | null): boolean {
  if (typeof document === 'undefined') return false;
  const current =
    document.fullscreenElement ??
    (document as Document & { webkitFullscreenElement?: Element | null }).webkitFullscreenElement ??
    null;
  return el ? current === el : current !== null;
}

/** Enter native fullscreen. Resolves false if the browser refused (caller falls back). */
export async function requestNativeFullscreen(el: Element): Promise<boolean> {
  const target = el as Element & {
    requestFullscreen?: () => Promise<void>;
    webkitRequestFullscreen?: () => Promise<void> | void;
  };
  try {
    if (typeof target.requestFullscreen === 'function') {
      await target.requestFullscreen();
      return true;
    }
    if (typeof target.webkitRequestFullscreen === 'function') {
      await target.webkitRequestFullscreen();
      return true;
    }
  } catch {
    // Refused (no user gesture, policy, …) — the caller uses the CSS fallback instead.
  }
  return false;
}

/** Leave native fullscreen if we're in it. Safe to call unconditionally. */
export async function exitNativeFullscreen(): Promise<void> {
  if (typeof document === 'undefined' || !isNativeFullscreen()) return;
  const doc = document as Document & { webkitExitFullscreen?: () => Promise<void> | void };
  try {
    if (typeof document.exitFullscreen === 'function') await document.exitFullscreen();
    else if (typeof doc.webkitExitFullscreen === 'function') await doc.webkitExitFullscreen();
  } catch {
    // Already exited, or the browser declined — nothing useful to do.
  }
}

/**
 * Subscribe to native fullscreen changes (covers the user pressing Esc or the system back
 * gesture, which bypass our button). Returns an unsubscribe function.
 */
export function onFullscreenChange(handler: () => void): () => void {
  if (typeof document === 'undefined') return () => {};
  document.addEventListener('fullscreenchange', handler);
  document.addEventListener('webkitfullscreenchange', handler);
  return () => {
    document.removeEventListener('fullscreenchange', handler);
    document.removeEventListener('webkitfullscreenchange', handler);
  };
}
