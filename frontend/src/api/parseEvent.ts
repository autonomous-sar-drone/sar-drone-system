import type { EventMessage, TelemetryData } from '../types/telemetry';

export type ParseResult =
  | { kind: 'ok'; message: EventMessage }
  /** Well-formed message of a type this build does not handle yet. Safe to ignore. */
  | { kind: 'unknown_type'; type: string }
  | { kind: 'invalid'; reason: string };

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function parseTelemetryData(raw: unknown): TelemetryData | string {
  if (typeof raw !== 'object' || raw === null) return 'data is not an object';
  const d = raw as Record<string, unknown>;

  if (typeof d.timestamp !== 'string' || Number.isNaN(Date.parse(d.timestamp))) {
    return 'timestamp is missing or not ISO-8601';
  }
  for (const field of ['latitudeDeg', 'longitudeDeg', 'relativeAltitudeM', 'groundSpeedMps'] as const) {
    if (!isFiniteNumber(d[field])) return `${field} is missing or not a finite number`;
  }
  if (d.headingDeg !== null && !isFiniteNumber(d.headingDeg)) {
    return 'headingDeg must be a finite number or null';
  }
  if (Math.abs(d.latitudeDeg as number) > 90) return 'latitudeDeg is out of range';
  if (Math.abs(d.longitudeDeg as number) > 180) return 'longitudeDeg is out of range';

  return {
    timestamp: d.timestamp,
    latitudeDeg: d.latitudeDeg as number,
    longitudeDeg: d.longitudeDeg as number,
    relativeAltitudeM: d.relativeAltitudeM as number,
    headingDeg: d.headingDeg as number | null,
    groundSpeedMps: d.groundSpeedMps as number,
  };
}

/** Parse one raw WebSocket frame. Never throws. */
export function parseEvent(raw: string): ParseResult {
  let json: unknown;
  try {
    json = JSON.parse(raw);
  } catch {
    return { kind: 'invalid', reason: 'frame is not valid JSON' };
  }
  if (typeof json !== 'object' || json === null) {
    return { kind: 'invalid', reason: 'frame is not a JSON object' };
  }
  const msg = json as Record<string, unknown>;
  if (typeof msg.type !== 'string') {
    return { kind: 'invalid', reason: 'message has no string "type" discriminator' };
  }

  switch (msg.type) {
    case 'telemetry_update': {
      const data = parseTelemetryData(msg.data);
      if (typeof data === 'string') return { kind: 'invalid', reason: `telemetry_update: ${data}` };
      return { kind: 'ok', message: { type: 'telemetry_update', data } };
    }
    default:
      return { kind: 'unknown_type', type: msg.type };
  }
}
