import { MANUAL_LIMITS, type FlightKey, type ManualAxes } from './manualIntent';

/**
 * Compact in-context key legend, shown only while the aircraft is under manual
 * control (PHASE 2).
 *
 * It lights up the keys actually being captured rather than the keys physically
 * held, so the operator can see at a glance when the console has stopped sending,
 * which is the moment a silently dead control channel would otherwise look normal.
 */

interface Row {
  keys: Array<{ cap: string; key: FlightKey }>;
  action: string;
  limit: string;
}

const ROWS: Row[] = [
  {
    keys: [
      { cap: 'W', key: 'W' },
      { cap: 'S', key: 'S' },
    ],
    action: 'Forward / back',
    limit: `${MANUAL_LIMITS.horizontalMps.toFixed(1)} m/s`,
  },
  {
    keys: [
      { cap: 'A', key: 'A' },
      { cap: 'D', key: 'D' },
    ],
    action: 'Left / right',
    limit: `${MANUAL_LIMITS.horizontalMps.toFixed(1)} m/s`,
  },
  {
    keys: [
      { cap: 'Q', key: 'Q' },
      { cap: 'E', key: 'E' },
    ],
    action: 'Yaw left / right',
    limit: `${MANUAL_LIMITS.yawDegPerSec}°/s`,
  },
  {
    keys: [
      { cap: 'Shift', key: 'UP' },
      { cap: 'Ctrl', key: 'DOWN' },
    ],
    action: 'Climb / descend',
    limit: `${MANUAL_LIMITS.verticalMps} m/s`,
  },
];

interface Props {
  held: FlightKey[];
  axes: ManualAxes;
  capturing: boolean;
  /** Null while manual input is working. */
  unavailableReason: string | null;
}

export function ManualFlightLegend({ held, capturing, unavailableReason }: Props) {
  const active = new Set(capturing ? held : []);
  return (
    <div className="manual-legend" aria-label="Manual flight keys">
      <div className="manual-legend__keys">
        {ROWS.map((row) => (
          <div key={row.action} className="manual-legend__row">
            <span className="manual-legend__caps">
              {row.keys.map(({ cap, key }) => (
                <kbd key={cap} className={active.has(key) ? 'is-active' : undefined}>
                  {cap}
                </kbd>
              ))}
            </span>
            <span className="manual-legend__action">{row.action}</span>
            <span className="manual-legend__limit">{row.limit}</span>
          </div>
        ))}
      </div>
      <p className="manual-legend__note">
        {unavailableReason ??
          `Escape clears input without giving control back. Height is held between ${MANUAL_LIMITS.minAltitudeM} and ${MANUAL_LIMITS.maxAltitudeM} m above takeoff.`}
      </p>
      {/* Spoken only when it changes, so a dropped channel is announced rather than
          left for the operator to notice from a motionless aircraft. */}
      <p className="sr-only" role="status">
        {unavailableReason ?? (capturing ? 'Manual input active.' : '')}
      </p>
    </div>
  );
}
