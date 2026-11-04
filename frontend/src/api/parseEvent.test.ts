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

describe('parseEvent: vision_status', () => {
  const data = {
    sessionId: 'vision-session-abc123',
    revision: 3,
    timestamp: '2026-11-02T20:00:00.000Z',
    camera: { state: 'LIVE', lastFrameAt: '2026-11-02T20:00:00.000Z' },
    detector: { state: 'UNAVAILABLE', lastInferenceAt: null },
    cameraOutageSeconds: null,
    automaticRtlTriggered: false,
  };
  const frame = (d: unknown) => parseEvent(JSON.stringify({ type: 'vision_status', data: d }));

  it('reads a healthy status', () => {
    const result = frame(data);
    expect(result.kind).toBe('ok');
    if (result.kind === 'ok' && result.message.type === 'vision_status') {
      expect(result.message.data.camera.state).toBe('LIVE');
      expect(result.message.data.detector.lastInferenceAt).toBeNull();
      expect(result.message.data.revision).toBe(3);
    }
  });

  it('accepts the explicit nulls this event sends', () => {
    // vision_status is serialized WITHOUT exclude_none, unlike the other two events.
    const result = frame({ ...data, cameraOutageSeconds: null, camera: { state: 'STARTING', lastFrameAt: null } });
    expect(result.kind).toBe('ok');
    if (result.kind === 'ok' && result.message.type === 'vision_status') {
      expect(result.message.data.cameraOutageSeconds).toBeNull();
      expect(result.message.data.camera.lastFrameAt).toBeNull();
    }
  });

  it('reads an outage with the automatic return home', () => {
    const result = frame({
      ...data,
      camera: { state: 'UNAVAILABLE', lastFrameAt: '2026-11-02T19:59:00.000Z' },
      cameraOutageSeconds: 61.5,
      automaticRtlTriggered: true,
    });
    expect(result.kind).toBe('ok');
    if (result.kind === 'ok' && result.message.type === 'vision_status') {
      expect(result.message.data.cameraOutageSeconds).toBeCloseTo(61.5);
      expect(result.message.data.automaticRtlTriggered).toBe(true);
    }
  });

  it('rejects a health state the frontend does not know', () => {
    const result = frame({ ...data, camera: { state: 'DEGRADED', lastFrameAt: null } });
    expect(result).toEqual({
      kind: 'invalid',
      reason: 'vision_status: camera.state is missing or unrecognized: DEGRADED',
    });
  });

  it('rejects a missing automaticRtlTriggered rather than assuming false', () => {
    const { automaticRtlTriggered: _omitted, ...rest } = data;
    expect(frame(rest).kind).toBe('invalid');
  });

  it('rejects a non-numeric outage', () => {
    expect(frame({ ...data, cameraOutageSeconds: 'a while' }).kind).toBe('invalid');
  });
});

describe('parseEvent: detection_update', () => {
  const candidate = {
    candidateId: 'cand-1',
    candidateNumber: 1,
    executionId: null,
    status: 'PENDING',
    firstObservedAt: '2026-11-04T15:00:00.000Z',
    capturedAt: '2026-11-04T15:00:01.000Z',
    publishedAt: '2026-11-04T15:00:02.000Z',
    reviewedAt: null,
    confidence: 0.91,
    boundingBox: {
      xPx: 10, yPx: 20, widthPx: 30, heightPx: 40, frameWidthPx: 1280, frameHeightPx: 720,
    },
    estimatedLocation: null,
    snapshotUrl: '/api/detections/cand-1/snapshot',
  };
  const frame = (d: unknown) => parseEvent(JSON.stringify({ type: 'detection_update', data: d }));

  it('reads a candidate update', () => {
    const result = frame({ sessionId: 's1', revision: 7, candidate });
    expect(result.kind).toBe('ok');
    if (result.kind === 'ok' && result.message.type === 'detection_update') {
      expect(result.message.data.revision).toBe(7);
      expect(result.message.data.candidate.candidateId).toBe('cand-1');
      expect(result.message.data.candidate.estimatedLocation).toBeNull();
    }
  });

  it('rejects an update whose candidate does not match the contract', () => {
    const result = frame({ sessionId: 's1', revision: 7, candidate: { candidateId: 'x' } });
    expect(result).toEqual({
      kind: 'invalid',
      reason: 'detection_update: candidate is missing or does not match the contract',
    });
  });

  it('rejects a missing revision', () => {
    expect(frame({ sessionId: 's1', candidate }).kind).toBe('invalid');
  });
});
