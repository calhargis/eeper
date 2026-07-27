/** Per-camera orientation: mirror horizontally, mirror vertically, rotate.
 *
 * A DISPLAY transform, applied with CSS to the video element. It costs nothing on the Pi —
 * no re-encode, no extra ffmpeg — and it takes effect the instant you press it, which is what
 * makes a live preview honest: you are looking at the real stream, transformed exactly the way
 * the Live view will transform it.
 *
 * The consequence to know: it does NOT change what is recorded. Clips keep the sensor's own
 * orientation. For a camera that is permanently mounted upside down, flipping it at the
 * adapter (the CSI adapter's HFLIP/VFLIP) fixes the footage too; this fixes the picture.
 *
 * Stored per camera in localStorage alongside the theme, so it is a per-device preference
 * rather than a household setting — two people can hold their phones differently.
 */

export type Rotation = 0 | 90 | 180 | 270;

export type CameraTransform = {
  flipH: boolean;
  flipV: boolean;
  rotation: Rotation;
};

export const IDENTITY: CameraTransform = { flipH: false, flipV: false, rotation: 0 };

const KEY = 'eeper:camera-transform';
const ROTATIONS: Rotation[] = [0, 90, 180, 270];

type Stored = Record<string, CameraTransform>;

function readAll(): Stored {
  if (typeof localStorage === 'undefined') return {};
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as Stored) : {};
  } catch {
    return {}; // corrupt or unavailable storage must never break the live view
  }
}

function isRotation(v: unknown): v is Rotation {
  return ROTATIONS.includes(v as Rotation);
}

/** The saved transform for a camera, or the identity. Never throws and never returns a
 * partially-valid object: a hand-edited localStorage entry falls back to identity rather than
 * producing a NaN in a CSS transform, which would blank the picture. */
export function loadTransform(cameraId: number): CameraTransform {
  const entry = readAll()[String(cameraId)];
  if (!entry || typeof entry !== 'object') return { ...IDENTITY };
  return {
    flipH: entry.flipH === true,
    flipV: entry.flipV === true,
    rotation: isRotation(entry.rotation) ? entry.rotation : 0,
  };
}

export function saveTransform(cameraId: number, t: CameraTransform): void {
  if (typeof localStorage === 'undefined') return;
  const all = readAll();
  if (t.flipH === false && t.flipV === false && t.rotation === 0) {
    delete all[String(cameraId)]; // don't persist the default
  } else {
    all[String(cameraId)] = t;
  }
  try {
    localStorage.setItem(KEY, JSON.stringify(all));
  } catch {
    /* private mode / quota — the transform simply won't persist */
  }
}

export function nextRotation(r: Rotation): Rotation {
  return ROTATIONS[(ROTATIONS.indexOf(r) + 1) % ROTATIONS.length];
}

export function isIdentity(t: CameraTransform): boolean {
  return !t.flipH && !t.flipV && t.rotation === 0;
}

/** How much a quarter-turned video must shrink to still fit its box.
 *
 * Rotating by 90° swaps the element's bounding box to height x width, which overflows the
 * container unless it is scaled down. `min(w/h, h/w)` is that factor for either orientation,
 * and it is exactly 1 when the box is square. Returns 1 for 0°/180°, which need no scaling. */
export function fitScale(rotation: Rotation, width: number, height: number): number {
  if (rotation !== 90 && rotation !== 270) return 1;
  if (!(width > 0) || !(height > 0)) return 1; // also catches NaN before it reaches CSS
  return Math.min(width / height, height / width);
}

/** The CSS `transform` value. Order matters: rotate first, then mirror, so "flip
 * horizontally" always means the horizontal axis of the picture you are LOOKING at rather
 * than of the sensor — which is what someone adjusting a rotated camera expects. */
export function cssTransform(t: CameraTransform, scale = 1): string {
  const parts: string[] = [];
  if (t.rotation !== 0) parts.push(`rotate(${t.rotation}deg)`);
  if (scale !== 1) parts.push(`scale(${scale})`);
  if (t.flipH) parts.push('scaleX(-1)');
  if (t.flipV) parts.push('scaleY(-1)');
  return parts.length ? parts.join(' ') : 'none';
}

/** A short human label, e.g. "Mirrored · 90°". Used so the settings row says what it is
 * doing rather than making the reader decode three toggles. */
export function describe(t: CameraTransform): string {
  const bits: string[] = [];
  if (t.flipH) bits.push('Mirrored');
  if (t.flipV) bits.push('Upside down');
  if (t.rotation !== 0) bits.push(`${t.rotation}°`);
  return bits.length ? bits.join(' · ') : 'Normal';
}
