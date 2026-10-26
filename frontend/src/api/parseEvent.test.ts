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
    expect(parseEvent(JSON.stringify({ type: 'coverage_update', data: {} }))).toEqual({
      kind: 'unknown_type',
      type: 'coverage_update',
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

describe('parseEvent: mission_update', () => {
  const data = {
    executionId: 'exec-1',
    planId: 'plan-1',
    missionState: 'RUNNING',
    timestamp: '2026-10-26T20:00:00Z',
    activeTarget: {
      latitudeDeg: 30.2291,
      longitudeDeg: -97.7555,
      altitudeM: 25,
      altitudeReference: 'HOME_RELATIVE',
    },
  };
  const frame = (over: Record<string, unknown> = {}) =>
    JSON.stringify({ type: 'mission_update', data: { ...data, ...over } });

  it('accepts a full update', () => {
    const result = parseEvent(frame());
    expect(result.kind).toBe('ok');
    if (result.kind === 'ok' && result.message.type === 'mission_update') {
      expect(result.message.data.missionState).toBe('RUNNING');
      expect(result.message.data.activeTarget?.altitudeM).toBe(25);
      expect(result.message.data.runtimeError).toBeNull();
    }
  });

  it('normalizes an omitted activeTarget to null', () => {
    // exclude_none on the backend means the key is absent, not null.
    const { activeTarget: _omitted, ...rest } = data;
    const result = parseEvent(JSON.stringify({ type: 'mission_update', data: rest }));
    expect(result.kind).toBe('ok');
    if (result.kind === 'ok' && result.message.type === 'mission_update') {
      expect(result.message.data.activeTarget).toBeNull();
    }
  });

  it('reads a runtime error when the backend reports one', () => {
    const result = parseEvent(
      frame({ runtimeError: { code: 'VEHICLE_NOT_READY', message: 'disarmed', details: { armed: false } } }),
    );
    expect(result.kind).toBe('ok');
    if (result.kind === 'ok' && result.message.type === 'mission_update') {
      expect(result.message.data.runtimeError?.code).toBe('VEHICLE_NOT_READY');
    }
  });

  it('rejects a mission state this build does not know', () => {
    const result = parseEvent(frame({ missionState: 'LANDING' }));
    expect(result.kind).toBe('invalid');
  });

  it('rejects a missing timestamp', () => {
    const { timestamp: _omitted, ...rest } = data;
    const result = parseEvent(JSON.stringify({ type: 'mission_update', data: rest }));
    expect(result.kind).toBe('invalid');
  });

  it('rejects a target measured against another datum', () => {
    const result = parseEvent(
      frame({ activeTarget: { ...data.activeTarget, altitudeReference: 'AMSL' } }),
    );
    expect(result.kind).toBe('invalid');
  });

  it('still ignores types this build does not handle', () => {
    const result = parseEvent(JSON.stringify({ type: 'coverage_update', data: {} }));
    expect(result).toEqual({ kind: 'unknown_type', type: 'coverage_update' });
  });
});
