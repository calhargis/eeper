import { describe as vdescribe, it, expect } from 'vitest';
import {
  IDENTITY,
  cssTransform,
  describe as label,
  fitScale,
  isIdentity,
  nextRotation,
} from './camera-transform';

vdescribe('fitScale', () => {
  it('leaves 0 and 180 alone — they need no shrinking', () => {
    expect(fitScale(0, 1600, 900)).toBe(1);
    expect(fitScale(180, 1600, 900)).toBe(1);
  });

  it('shrinks a quarter turn so the swapped bounding box still fits', () => {
    // A 16:9 box rotated 90 becomes 9:16, which only fits if scaled by 9/16.
    expect(fitScale(90, 1600, 900)).toBeCloseTo(900 / 1600);
    expect(fitScale(270, 1600, 900)).toBeCloseTo(900 / 1600);
  });

  it('is 1 for a square box, which a quarter turn does not change', () => {
    expect(fitScale(90, 500, 500)).toBe(1);
  });

  it('never emits NaN into a CSS transform when the box has not been measured', () => {
    // A zero or unmeasured size must degrade to "no scaling", not to scale(NaN), which
    // would blank the picture entirely.
    expect(fitScale(90, 0, 900)).toBe(1);
    expect(fitScale(90, Number.NaN, 900)).toBe(1);
  });
});

vdescribe('cssTransform', () => {
  it('is none for the identity, so no compositing layer is forced', () => {
    expect(cssTransform(IDENTITY)).toBe('none');
  });

  it('rotates before mirroring, so "flip horizontally" follows the picture not the sensor', () => {
    expect(cssTransform({ flipH: true, flipV: false, rotation: 90 }, 0.5)).toBe(
      'rotate(90deg) scale(0.5) scaleX(-1)',
    );
  });

  it('composes both mirrors', () => {
    expect(cssTransform({ flipH: true, flipV: true, rotation: 0 })).toBe('scaleX(-1) scaleY(-1)');
  });
});

vdescribe('rotation cycling', () => {
  it('walks the quarter turns and returns home', () => {
    expect(nextRotation(0)).toBe(90);
    expect(nextRotation(90)).toBe(180);
    expect(nextRotation(180)).toBe(270);
    expect(nextRotation(270)).toBe(0);
  });
});

vdescribe('labels', () => {
  it('names the default plainly', () => {
    expect(label(IDENTITY)).toBe('Normal');
    expect(isIdentity(IDENTITY)).toBe(true);
  });

  it('describes a combination', () => {
    expect(label({ flipH: true, flipV: false, rotation: 90 })).toBe('Mirrored · 90°');
  });
});
