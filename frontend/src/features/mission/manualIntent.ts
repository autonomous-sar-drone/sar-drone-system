/**
 * Manual flight key intent (PHASE 2), kept free of React and the DOM.
 *
 * Everything here is a pure function over a list of currently-held keys, so the
 * awkward rules (opposite keys on one axis, diagonal speed, repeat suppression) can
 * be tested directly instead of through simulated keyboard events.
 */

/** -1, 0 or +1 on each axis, exactly as the manual_input wire contract carries them. */
export interface ManualAxes {
  /** +1 forward (W), -1 backward (S). Drone-heading relative. */
  forward: -1 | 0 | 1;
  /** +1 right (D), -1 left (A). Translation, not a roll input. */
  right: -1 | 0 | 1;
  /** +1 climb (Shift), -1 descend (Ctrl). Rate command, not raw throttle. */
  up: -1 | 0 | 1;
  /** +1 yaw right (E), -1 yaw left (Q). */
  yaw: -1 | 0 | 1;
}

export const NEUTRAL_AXES: ManualAxes = { forward: 0, right: 0, up: 0, yaw: 0 };

export function axesEqual(a: ManualAxes, b: ManualAxes): boolean {
  return a.forward === b.forward && a.right === b.right && a.up === b.up && a.yaw === b.yaw;
}

export function isNeutral(axes: ManualAxes): boolean {
  return axesEqual(axes, NEUTRAL_AXES);
}

/** The eight keys that mean something in manual flight. */
export type FlightKey = 'W' | 'S' | 'A' | 'D' | 'Q' | 'E' | 'UP' | 'DOWN';

type AxisName = 'forward' | 'right' | 'up' | 'yaw';

const KEY_AXIS: Record<FlightKey, { axis: AxisName; sign: -1 | 1 }> = {
  W: { axis: 'forward', sign: 1 },
  S: { axis: 'forward', sign: -1 },
  D: { axis: 'right', sign: 1 },
  A: { axis: 'right', sign: -1 },
  E: { axis: 'yaw', sign: 1 },
  Q: { axis: 'yaw', sign: -1 },
  UP: { axis: 'up', sign: 1 },
  DOWN: { axis: 'up', sign: -1 },
};

/**
 * Backend-enforced initial manual speeds, repeated here only so the on-screen legend
 * tells the operator the same numbers the aircraft will actually fly. These are
 * display values; nothing here is sent on the wire.
 */
export const MANUAL_LIMITS = {
  horizontalMps: 2.0,
  verticalMps: 0.75,
  yawDegPerSec: 20,
  minAltitudeM: 5,
  maxAltitudeM: 40,
} as const;

/**
 * Map a keyboard event to a flight key, or null if it is not one.
 *
 * Uses `code` for the letters so the mapping stays physically WASD on a keyboard
 * layout where those positions carry different letters, and falls back to `key` for
 * the modifiers, which have no meaningful position.
 */
export function flightKeyFor(event: Pick<KeyboardEvent, 'key' | 'code'>): FlightKey | null {
  switch (event.code) {
    case 'KeyW':
      return 'W';
    case 'KeyS':
      return 'S';
    case 'KeyA':
      return 'A';
    case 'KeyD':
      return 'D';
    case 'KeyQ':
      return 'Q';
    case 'KeyE':
      return 'E';
    default:
      break;
  }
  if (event.key === 'Shift') return 'UP';
  if (event.key === 'Control') return 'DOWN';
  return null;
}

/**
 * Record a press, preserving order.
 *
 * Order is the whole point: on one axis the FIRST key pressed wins for as long as it
 * is held, so an operator who rolls a finger from W onto S does not get a lurch in
 * the opposite direction until they actually let go of W.
 *
 * A key already held is returned unchanged, which is what makes OS key-repeat a
 * no-op rather than something that reshuffles priority.
 */
export function pressKey(held: readonly FlightKey[], key: FlightKey): FlightKey[] {
  if (held.includes(key)) return held as FlightKey[];
  return [...held, key];
}

/** Release one key. The next still-held key on that axis takes effect immediately. */
export function releaseKey(held: readonly FlightKey[], key: FlightKey): FlightKey[] {
  return held.filter((k) => k !== key);
}

/**
 * Collapse held keys into axes.
 *
 * Per axis, the earliest still-held key decides the sign. Different axes are
 * independent, so movement, vertical and yaw combine freely.
 */
export function axesFor(held: readonly FlightKey[]): ManualAxes {
  const axes: ManualAxes = { ...NEUTRAL_AXES };
  const decided = new Set<AxisName>();
  for (const key of held) {
    const mapping = KEY_AXIS[key];
    if (decided.has(mapping.axis)) continue;
    decided.add(mapping.axis);
    axes[mapping.axis] = mapping.sign;
  }
  return axes;
}

/**
 * Horizontal speed the aircraft will actually fly, for display only.
 *
 * A diagonal is two unit axes, which would be 2.83 m/s if the components were simply
 * added. The backend normalizes the vector to the 2.0 m/s total, so the legend shows
 * the normalized figure: telling the operator a speed the aircraft will not fly is
 * worse than telling them nothing.
 */
export function horizontalSpeedMps(axes: ManualAxes): number {
  const magnitude = Math.hypot(axes.forward, axes.right);
  return magnitude === 0 ? 0 : MANUAL_LIMITS.horizontalMps;
}

/**
 * Whether a key event came from somewhere the operator is entering data.
 *
 * Flight keys are plain letters, so capturing them inside a text field would eat the
 * operator's typing. Buttons and links are deliberately NOT excluded: the operator
 * clicks Take Controls and then flies, and stealing their first keypress because
 * focus happens to still be on the button they just pressed would be its own bug.
 */
export function isTextEntryContext(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  if (target.closest('input, textarea, select, option, [contenteditable=""], [contenteditable="true"]')) {
    return true;
  }
  // A dialog or menu owns the keyboard while it is open, including Escape.
  return target.closest('[role="dialog"], [role="menu"], [role="listbox"], [aria-modal="true"]') !== null;
}
