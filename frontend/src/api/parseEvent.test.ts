import { describe, expect, it } from 'vitest';
import { parseEvent } from './parseEvent';

const valid = {
  type: 'telemetry_update',
  data: {
    timestamp: '2026-09-30T18:00:00.000Z',
    latitudeDeg: 30.2291,
    longitudeDeg: -97.7555,
    relativeAltitudeM: 25,
    headingDeg: 90,
    groundSpeedMps: 2,
  },
};

const frame = (overrides: Record<string, unknown>) =>
  JSON.stringify({ ...valid, data: { ...valid.data, ...overrides } });

describe('parseEvent', () => {
  it('accepts a contract-shaped telemetry_update', () => {
    const result = parseEvent(JSON.stringify(valid));
    expect(result).toEqual({ kind: 'ok', message: valid });
  });

  it('accepts null heading (unknown sentinel from the aircraft)', () => {
    const result = parseEvent(frame({ headingDeg: null }));
    expect(result.kind).toBe('ok');
  });

  it('ignores message types not handled yet instead of failing', () => {
    expect(parseEvent(JSON.stringify({ type: 'mission_update', data: {} }))).toEqual({
      kind: 'unknown_type',
      type: 'mission_update',
    });
  });

  it.each([
    ['not JSON', 'nope'],
    ['no type', JSON.stringify({ data: valid.data })],
    ['missing latitude', frame({ latitudeDeg: undefined })],
    ['string number', frame({ groundSpeedMps: '2' })],
    ['NaN-like heading', frame({ headingDeg: 'unknown' })],
    ['bad timestamp', frame({ timestamp: 'yesterday' })],
    ['latitude out of range', frame({ latitudeDeg: 91 })],
    ['snake_case backend field leaked', JSON.stringify({ type: 'telemetry_update', data: { latitude_deg: 1 } })],
  ])('rejects %s', (_label, raw) => {
    expect(parseEvent(raw).kind).toBe('invalid');
  });
});
