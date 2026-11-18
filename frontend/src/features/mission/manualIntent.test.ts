import { describe, expect, it } from 'vitest';
import {
  NEUTRAL_AXES,
  axesEqual,
  axesFor,
  flightKeyFor,
  horizontalSpeedMps,
  isNeutral,
  isTextEntryContext,
  pressKey,
  releaseKey,
  MANUAL_LIMITS,
  type FlightKey,
} from './manualIntent';

const key = (code: string, k = '') => ({ code, key: k });

/** Press a sequence in order, as the operator would. */
function hold(...keys: FlightKey[]): FlightKey[] {
  return keys.reduce<FlightKey[]>((held, k) => pressKey(held, k), []);
}

describe('flightKeyFor', () => {
  it('maps every flight key, including the modifier names', () => {
    expect(flightKeyFor(key('KeyW'))).toBe('W');
    expect(flightKeyFor(key('KeyS'))).toBe('S');
    expect(flightKeyFor(key('KeyA'))).toBe('A');
    expect(flightKeyFor(key('KeyD'))).toBe('D');
    expect(flightKeyFor(key('KeyQ'))).toBe('Q');
    expect(flightKeyFor(key('KeyE'))).toBe('E');
    expect(flightKeyFor(key('ShiftLeft', 'Shift'))).toBe('UP');
    expect(flightKeyFor(key('ControlRight', 'Control'))).toBe('DOWN');
  });

  it('ignores keys that are not flight controls', () => {
    expect(flightKeyFor(key('KeyR'))).toBeNull();
    expect(flightKeyFor(key('Space', ' '))).toBeNull();
    expect(flightKeyFor(key('Escape', 'Escape'))).toBeNull();
    expect(flightKeyFor(key('AltLeft', 'Alt'))).toBeNull();
  });

  it('follows the physical key position, not the printed letter', () => {
    // On AZERTY the W position reports key "z". Steering must stay under the same
    // finger, so the mapping reads code and ignores key for letters.
    expect(flightKeyFor({ code: 'KeyW', key: 'z' })).toBe('W');
  });
});

describe('axesFor', () => {
  it('is neutral with nothing held', () => {
    expect(axesFor([])).toEqual(NEUTRAL_AXES);
    expect(isNeutral(axesFor([]))).toBe(true);
  });

  it('maps each key to its documented axis and sign', () => {
    expect(axesFor(['W']).forward).toBe(1);
    expect(axesFor(['S']).forward).toBe(-1);
    expect(axesFor(['D']).right).toBe(1);
    expect(axesFor(['A']).right).toBe(-1);
    expect(axesFor(['UP']).up).toBe(1);
    expect(axesFor(['DOWN']).up).toBe(-1);
    expect(axesFor(['E']).yaw).toBe(1);
    expect(axesFor(['Q']).yaw).toBe(-1);
  });

  it('combines movement, vertical and yaw independently', () => {
    expect(axesFor(hold('W', 'D', 'UP', 'E'))).toEqual({
      forward: 1,
      right: 1,
      up: 1,
      yaw: 1,
    });
  });
});

describe('opposite keys on one axis', () => {
  it('keeps the first pressed key while it is still held', () => {
    // W then S: the operator has not let go of W, so the aircraft must not lurch
    // backwards the instant their other finger lands.
    expect(axesFor(hold('W', 'S')).forward).toBe(1);
    expect(axesFor(hold('S', 'W')).forward).toBe(-1);
  });

  it('hands the axis to the opposite key still held once the first is released', () => {
    const both = hold('W', 'S');
    expect(axesFor(releaseKey(both, 'W')).forward).toBe(-1);
  });

  it('returns to neutral when both are released', () => {
    let held = hold('W', 'S');
    held = releaseKey(held, 'W');
    held = releaseKey(held, 'S');
    expect(isNeutral(axesFor(held))).toBe(true);
  });

  it('resolves each axis on its own', () => {
    // Contested forward axis, uncontested right axis.
    expect(axesFor(hold('W', 'S', 'D'))).toEqual({ forward: 1, right: 1, up: 0, yaw: 0 });
  });

  it('applies the same rule to yaw and to the vertical modifiers', () => {
    expect(axesFor(hold('Q', 'E')).yaw).toBe(-1);
    expect(axesFor(hold('UP', 'DOWN')).up).toBe(1);
    expect(axesFor(releaseKey(hold('UP', 'DOWN'), 'UP')).up).toBe(-1);
  });
});

describe('press ordering', () => {
  it('treats a repeated press of a held key as a no-op', () => {
    // OS key repeat fires keydown again. If that re-ordered the list, holding W and
    // then tapping S would flip the axis on the next repeat of S.
    const held = hold('W', 'S');
    expect(pressKey(held, 'S')).toBe(held);
    expect(axesFor(pressKey(held, 'S')).forward).toBe(1);
  });

  it('restores priority when a key is released and pressed again', () => {
    let held = hold('W', 'S');
    held = releaseKey(held, 'W');
    held = pressKey(held, 'W');
    // S is now the earlier of the two.
    expect(axesFor(held).forward).toBe(-1);
  });

  it('ignores a release for a key that is not held', () => {
    expect(releaseKey(['W'], 'S')).toEqual(['W']);
  });
});

describe('horizontalSpeedMps', () => {
  it('is zero when no movement key is held', () => {
    expect(horizontalSpeedMps(axesFor(['UP', 'E']))).toBe(0);
  });

  it('reports the same total for a straight line and a diagonal', () => {
    // Two unit axes would be 2.83 m/s if simply added. The backend normalizes the
    // vector, so the legend must show the normalized figure or it is lying.
    const straight = horizontalSpeedMps(axesFor(['W']));
    const diagonal = horizontalSpeedMps(axesFor(hold('W', 'D')));
    expect(straight).toBe(MANUAL_LIMITS.horizontalMps);
    expect(diagonal).toBe(MANUAL_LIMITS.horizontalMps);
    expect(diagonal).toBeLessThan(Math.hypot(2, 2));
  });
});

describe('axesEqual', () => {
  it('compares every axis', () => {
    expect(axesEqual(axesFor(['W']), axesFor(['W']))).toBe(true);
    expect(axesEqual(axesFor(['W']), axesFor(['S']))).toBe(false);
    expect(axesEqual(axesFor(['W']), axesFor(hold('W', 'E')))).toBe(false);
  });
});

describe('isTextEntryContext', () => {
  function el(html: string): Element {
    const host = document.createElement('div');
    host.innerHTML = html;
    document.body.appendChild(host);
    return host.firstElementChild as Element;
  }

  it('is true inside the places an operator types', () => {
    expect(isTextEntryContext(el('<input />'))).toBe(true);
    expect(isTextEntryContext(el('<textarea></textarea>'))).toBe(true);
    expect(isTextEntryContext(el('<select><option>a</option></select>'))).toBe(true);
    expect(isTextEntryContext(el('<div contenteditable="true"><span>x</span></div>').firstElementChild)).toBe(true);
  });

  it('is true inside a dialog, which owns the keyboard while open', () => {
    expect(isTextEntryContext(el('<div role="dialog"><button>ok</button></div>').firstElementChild)).toBe(true);
  });

  it('is false for buttons and the map, so flying still works right after a click', () => {
    // The operator clicks Take Controls and then flies. Treating the still-focused
    // button as a text context would swallow their first keypress.
    expect(isTextEntryContext(el('<button>Take controls</button>'))).toBe(false);
    expect(isTextEntryContext(el('<canvas></canvas>'))).toBe(false);
    expect(isTextEntryContext(null)).toBe(false);
  });
});
