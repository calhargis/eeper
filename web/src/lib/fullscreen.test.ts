import { describe, expect, it } from 'vitest';
import { pickFullscreenStrategy } from './fullscreen';

// The strategy choice is what keeps fullscreen working on iPhone Safari, which has no
// Element Fullscreen API — it must land on the CSS fallback rather than calling a method
// that doesn't exist (or one the document forbids).
describe('pickFullscreenStrategy', () => {
  const req = () => {};

  it('uses the native API when the element and document both support it', () => {
    expect(pickFullscreenStrategy({ requestFullscreen: req }, { fullscreenEnabled: true })).toBe(
      'native',
    );
  });

  it('accepts the webkit-prefixed pair (older Safari / iPad)', () => {
    expect(
      pickFullscreenStrategy({ webkitRequestFullscreen: req }, { webkitFullscreenEnabled: true }),
    ).toBe('native');
  });

  it('falls back on iPhone Safari — no request method on the element', () => {
    expect(pickFullscreenStrategy({}, { fullscreenEnabled: false })).toBe('faux');
  });

  it('falls back when the document disallows fullscreen even though the method exists', () => {
    // e.g. an iframe without allowfullscreen: calling it would just reject.
    expect(pickFullscreenStrategy({ requestFullscreen: req }, { fullscreenEnabled: false })).toBe(
      'faux',
    );
  });

  it('falls back when fullscreenEnabled is missing entirely', () => {
    expect(pickFullscreenStrategy({ requestFullscreen: req }, {})).toBe('faux');
  });

  it('falls back for a null element or document (SSR / not yet mounted)', () => {
    expect(pickFullscreenStrategy(null, { fullscreenEnabled: true })).toBe('faux');
    expect(pickFullscreenStrategy({ requestFullscreen: req }, null)).toBe('faux');
    expect(pickFullscreenStrategy(undefined, undefined)).toBe('faux');
  });

  it('ignores a non-callable requestFullscreen property', () => {
    expect(
      pickFullscreenStrategy({ requestFullscreen: 'nope' as unknown }, { fullscreenEnabled: true }),
    ).toBe('faux');
  });
});
