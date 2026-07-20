// Client-side theming. A theme is just a handful of BASE color tokens; app.css
// derives every other token from them with color-mix(), so "applying" a theme means
// setting ~8 CSS custom properties on <html> plus a data-theme attribute (which picks
// the color-scheme and the mode's shadow set). Users choose a preset or build a fully
// custom palette with per-category HSL sliders. The choice is a personal, per-device
// preference — persisted in localStorage, no backend. The inline <head> script in
// app.html applies the stored theme before first paint to avoid a flash of the default.

export type Mode = 'dark' | 'light';

// The color categories a custom palette exposes (each gets Hue/Saturation/Lightness
// sliders). Everything else (borders, muted text, subtle fills, accent-ink) derives.
export type Category = 'bg' | 'surface' | 'text' | 'accent' | 'warn' | 'danger';

export const CATEGORIES: { key: Category; label: string; hint: string }[] = [
  { key: 'bg', label: 'Background', hint: 'The page behind everything' },
  { key: 'surface', label: 'Cards', hint: 'Panels and raised surfaces' },
  { key: 'text', label: 'Text', hint: 'Primary reading color' },
  { key: 'accent', label: 'Accent', hint: 'Buttons, links, highlights' },
  { key: 'warn', label: 'Warning', hint: 'Cautions and quiet-hours' },
  { key: 'danger', label: 'Alert', hint: 'Errors and distress' },
];

// The 8 base tokens a theme sets (keys are token names without the leading `--`).
export interface BaseVars {
  bg: string;
  surface: string;
  text: string;
  accent: string;
  'accent-ink': string;
  warn: string;
  danger: string;
  ok: string;
}

export interface Theme {
  id: string; // a preset id, or 'custom'
  name: string;
  mode: Mode;
  vars: BaseVars;
}

const STORAGE_KEY = 'eeper-theme';

// ── presets ──────────────────────────────────────────────────────────────────
// A spread of moods, dark and light. `id` is stable (persisted); order is display order.
export const PRESETS: Theme[] = [
  {
    id: 'nightlight',
    name: 'Calm nightlight',
    mode: 'dark',
    vars: {
      bg: '#14110f',
      surface: '#201b18',
      text: '#f2ece3',
      accent: '#6fd6c4',
      'accent-ink': '#08130f',
      warn: '#f0b24a',
      danger: '#f28b7a',
      ok: '#7dd6a4',
    },
  },
  {
    id: 'midnight',
    name: 'Midnight',
    mode: 'dark',
    vars: {
      bg: '#0f1220',
      surface: '#1a1e33',
      text: '#e8ecf7',
      accent: '#7c8cf8',
      'accent-ink': '#0a0f22',
      warn: '#f0b24a',
      danger: '#f2857a',
      ok: '#6fd6a0',
    },
  },
  {
    id: 'amethyst',
    name: 'Amethyst',
    mode: 'dark',
    vars: {
      bg: '#150f1f',
      surface: '#211830',
      text: '#efe9f7',
      accent: '#b088f0',
      'accent-ink': '#12081f',
      warn: '#f0b24a',
      danger: '#f2857a',
      ok: '#86d6b0',
    },
  },
  {
    id: 'forest',
    name: 'Forest',
    mode: 'dark',
    vars: {
      bg: '#0f1512',
      surface: '#18211c',
      text: '#e9f0e8',
      accent: '#64d19b',
      'accent-ink': '#06140d',
      warn: '#e8b74f',
      danger: '#ef8a78',
      ok: '#7dd6a4',
    },
  },
  {
    id: 'ember',
    name: 'Ember',
    mode: 'dark',
    vars: {
      bg: '#17120c',
      surface: '#241b12',
      text: '#f6ecdd',
      accent: '#f0a935',
      'accent-ink': '#1c1204',
      warn: '#e8b74f',
      danger: '#f28b7a',
      ok: '#7dd6a4',
    },
  },
  {
    id: 'rosewood',
    name: 'Rosewood',
    mode: 'dark',
    vars: {
      bg: '#1a1013',
      surface: '#271a1e',
      text: '#f6e9ec',
      accent: '#f2879f',
      'accent-ink': '#1a0910',
      warn: '#f0b24a',
      danger: '#f2707a',
      ok: '#86d6a8',
    },
  },
  {
    id: 'daylight',
    name: 'Daylight',
    mode: 'light',
    vars: {
      bg: '#f6f1ea',
      surface: '#fffdf9',
      text: '#2a231d',
      accent: '#12a493',
      'accent-ink': '#ffffff',
      warn: '#b26a12',
      danger: '#c0492f',
      ok: '#2f9e6b',
    },
  },
  {
    id: 'parchment',
    name: 'Parchment',
    mode: 'light',
    vars: {
      bg: '#f4ece0',
      surface: '#fffaf0',
      text: '#33291d',
      accent: '#c07a2b',
      'accent-ink': '#ffffff',
      warn: '#b26a12',
      danger: '#bd4436',
      ok: '#3a8f5f',
    },
  },
];

// ── color helpers ────────────────────────────────────────────────────────────
export interface Hsl {
  h: number; // 0–360
  s: number; // 0–100
  l: number; // 0–100
}

function clamp(n: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, n));
}

export function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace('#', '');
  const v =
    h.length === 3
      ? h
          .split('')
          .map((c) => c + c)
          .join('')
      : h;
  return [parseInt(v.slice(0, 2), 16), parseInt(v.slice(2, 4), 16), parseInt(v.slice(4, 6), 16)];
}

function toHex2(n: number): string {
  return clamp(Math.round(n), 0, 255).toString(16).padStart(2, '0');
}

export function rgbToHex(r: number, g: number, b: number): string {
  return `#${toHex2(r)}${toHex2(g)}${toHex2(b)}`;
}

export function hexToHsl(hex: string): Hsl {
  const [r, g, b] = hexToRgb(hex).map((n) => n / 255) as [number, number, number];
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const d = max - min;
  let h = 0;
  if (d !== 0) {
    if (max === r) h = ((g - b) / d) % 6;
    else if (max === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
    h *= 60;
    if (h < 0) h += 360;
  }
  const l = (max + min) / 2;
  const s = d === 0 ? 0 : d / (1 - Math.abs(2 * l - 1));
  return { h: Math.round(h), s: Math.round(s * 100), l: Math.round(l * 100) };
}

export function hslToHex({ h, s, l }: Hsl): string {
  const sn = s / 100;
  const ln = l / 100;
  const c = (1 - Math.abs(2 * ln - 1)) * sn;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = ln - c / 2;
  let r = 0;
  let g = 0;
  let b = 0;
  if (h < 60) [r, g, b] = [c, x, 0];
  else if (h < 120) [r, g, b] = [x, c, 0];
  else if (h < 180) [r, g, b] = [0, c, x];
  else if (h < 240) [r, g, b] = [0, x, c];
  else if (h < 300) [r, g, b] = [x, 0, c];
  else [r, g, b] = [c, 0, x];
  return rgbToHex((r + m) * 255, (g + m) * 255, (b + m) * 255);
}

// Relative luminance (WCAG) for picking a readable ink and inferring dark/light mode.
export function luminance(hex: string): number {
  const [r, g, b] = hexToRgb(hex).map((n) => {
    const c = n / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  }) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

// A high-contrast ink for text sitting on top of `hex` (dark ink on light accents,
// light ink on dark accents) — tinted slightly by the accent so it feels intentional.
export function inkFor(hex: string): string {
  const { h } = hexToHsl(hex);
  return luminance(hex) > 0.42 ? hslToHex({ h, s: 45, l: 10 }) : hslToHex({ h, s: 30, l: 97 });
}

// Build a full custom theme from the 6 editable HSL categories. Mode is inferred from
// whether the background is darker than the text; ok/accent-ink are derived.
export function customTheme(hsl: Record<Category, Hsl>): Theme {
  const vars: BaseVars = {
    bg: hslToHex(hsl.bg),
    surface: hslToHex(hsl.surface),
    text: hslToHex(hsl.text),
    accent: hslToHex(hsl.accent),
    'accent-ink': inkFor(hslToHex(hsl.accent)),
    warn: hslToHex(hsl.warn),
    danger: hslToHex(hsl.danger),
    ok: '#7dd6a4',
  };
  const mode: Mode = luminance(vars.bg) < luminance(vars.text) ? 'dark' : 'light';
  vars.ok = mode === 'dark' ? '#7dd6a4' : '#2f9e6b';
  return { id: 'custom', name: 'Custom', mode, vars };
}

// Seed the slider state for the custom editor from an existing theme's base colors.
export function toHslMap(vars: BaseVars): Record<Category, Hsl> {
  return {
    bg: hexToHsl(vars.bg),
    surface: hexToHsl(vars.surface),
    text: hexToHsl(vars.text),
    accent: hexToHsl(vars.accent),
    warn: hexToHsl(vars.warn),
    danger: hexToHsl(vars.danger),
  };
}

// ── apply + persist ──────────────────────────────────────────────────────────
export function applyTheme(theme: Theme): void {
  if (typeof document === 'undefined') return;
  const root = document.documentElement;
  root.setAttribute('data-theme', theme.mode);
  for (const [k, v] of Object.entries(theme.vars)) root.style.setProperty(`--${k}`, v);
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', theme.vars.bg);
}

// Drop back to following the system (light/dark) — clears the override and inline vars.
export function clearTheme(): void {
  if (typeof document === 'undefined') return;
  const root = document.documentElement;
  root.removeAttribute('data-theme');
  for (const k of ['bg', 'surface', 'text', 'accent', 'accent-ink', 'warn', 'danger', 'ok'])
    root.style.removeProperty(`--${k}`);
}

export function saveTheme(theme: Theme): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(theme));
  } catch {
    /* private mode / storage disabled — theme just won't persist */
  }
}

export function clearSaved(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
}

export function loadTheme(): Theme | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const t = JSON.parse(raw) as Theme;
    return t && t.vars && t.mode ? t : null;
  } catch {
    return null;
  }
}
