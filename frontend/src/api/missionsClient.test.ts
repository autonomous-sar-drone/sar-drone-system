import { describe, expect, it, vi } from 'vitest';
import {
  cancelRevisit,
  commenceMission,
  continueMission,
  giveControls,
  parseExecution,
  parseMissionError,
  pauseMission,
  resumeMission,
  revisitDetection,
  stopMission,
  takeControls,
} from './missionsClient';

const EXECUTION = {
  executionId: 'exec-1',
  planId: 'plan-1',
  missionState: 'RUNNING',
  activeTarget: {
    latitudeDeg: 30.2291,
    longitudeDeg: -97.7555,
    altitudeM: 25,
    altitudeReference: 'HOME_RELATIVE',
  },
};

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

describe('parseExecution', () => {
  it('reads a full execution', () => {
    const parsed = parseExecution(EXECUTION);
    expect(parsed?.executionId).toBe('exec-1');
    expect(parsed?.missionState).toBe('RUNNING');
    expect(parsed?.activeTarget?.altitudeM).toBe(25);
  });

  it('treats an absent activeTarget as no target', () => {
    // The backend serializes with exclude_none, so the key is missing rather than null.
    const { activeTarget: _omitted, ...withoutTarget } = EXECUTION;
    expect(parseExecution(withoutTarget)?.activeTarget).toBeNull();
    expect(parseExecution({ ...EXECUTION, activeTarget: null })?.activeTarget).toBeNull();
  });

  it('rejects a mission state the frontend does not know', () => {
    expect(parseExecution({ ...EXECUTION, missionState: 'LANDING' })).toBeNull();
  });

  it('rejects a missing execution id', () => {
    const { executionId: _omitted, ...withoutId } = EXECUTION;
    expect(parseExecution(withoutId)).toBeNull();
  });

  it('rejects an active target measured against another datum', () => {
    const target = { ...EXECUTION.activeTarget, altitudeReference: 'AMSL' };
    expect(parseExecution({ ...EXECUTION, activeTarget: target })?.activeTarget).toBeNull();
  });
});

describe('parseMissionError', () => {
  it('reads the shared detail envelope', () => {
    const error = parseMissionError(409, {
      detail: { code: 'VEHICLE_NOT_READY', message: 'not armed', details: { armed: false } },
    });
    expect(error.code).toBe('VEHICLE_NOT_READY');
    expect(error.details).toEqual({ armed: false });
  });

  it('falls back when the body is not the agreed shape', () => {
    const error = parseMissionError(422, { detail: [{ loc: ['body'] }] });
    expect(error.code).toBe('UNEXPECTED_RESPONSE');
  });
});

describe('mission commands', () => {
  it('posts the plan id to commence', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse(EXECUTION));
    const result = await commenceMission('plan-1', { fetchImpl });
    expect(fetchImpl).toHaveBeenCalledWith('/api/missions/commence', expect.any(Object));
    expect(JSON.parse(fetchImpl.mock.calls[0][1].body)).toEqual({ planId: 'plan-1' });
    expect(result).toEqual({ ok: true, execution: parseExecution(EXECUTION) });
  });

  it.each([
    [pauseMission, '/api/missions/exec-1/pause'],
    [resumeMission, '/api/missions/exec-1/resume'],
    [stopMission, '/api/missions/exec-1/stop'],
    [continueMission, '/api/missions/exec-1/continue'],
  ])('targets the right route', async (command, path) => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse(EXECUTION));
    await command('exec-1', { fetchImpl });
    expect(fetchImpl).toHaveBeenCalledWith(path, expect.any(Object));
  });

  it('escapes the execution id in the path', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse(EXECUTION));
    await pauseMission('a/b', { fetchImpl });
    expect(fetchImpl.mock.calls[0][0]).toBe('/api/missions/a%2Fb/pause');
  });

  it('surfaces a 409 as a structured error', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      jsonResponse({ detail: { code: 'MISSION_ACTION_NOT_ALLOWED', message: 'no', details: {} } }, 409),
    );
    const result = await pauseMission('exec-1', { fetchImpl });
    expect(result).toEqual({
      ok: false,
      error: { code: 'MISSION_ACTION_NOT_ALLOWED', message: 'no', details: {} },
    });
  });

  it('reports an unreachable backend rather than throwing', async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new TypeError('failed to fetch'));
    const result = await stopMission('exec-1', { fetchImpl });
    expect(result).toEqual({
      ok: false,
      error: { code: 'NETWORK_ERROR', message: 'Could not reach the backend.', details: {} },
    });
  });

  it('rethrows an abort so the caller can ignore it', async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new DOMException('aborted', 'AbortError'));
    await expect(pauseMission('exec-1', { fetchImpl })).rejects.toThrow('aborted');
  });

  it('rejects a 200 body that is not a mission', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse({ nope: true }));
    const result = await pauseMission('exec-1', { fetchImpl });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error.code).toBe('UNEXPECTED_RESPONSE');
  });
});

describe('parseExecution interruptionReason', () => {
  it('carries the reason from an HTTP response too', () => {
    const parsed = parseExecution({ ...EXECUTION, interruptionReason: 'OPERATOR_STOP' });
    expect(parsed?.interruptionReason).toBe('OPERATOR_STOP');
  });

  it('is null when absent or unrecognized', () => {
    expect(parseExecution(EXECUTION)?.interruptionReason).toBeNull();
    expect(parseExecution({ ...EXECUTION, interruptionReason: 'NOPE' })?.interruptionReason).toBeNull();
  });
});

/* ------------------------------------------------------------------ PHASE 2 */

describe('parseExecution with the Phase 2 fields', () => {
  it('reads activity, hold reason, revisit target and readiness', () => {
    const parsed = parseExecution({
      ...EXECUTION,
      missionState: 'PAUSED',
      flightActivity: 'REVISITING',
      holdReason: 'REVISIT_ARRIVAL',
      activeRevisitCandidateId: 'cand-4',
      manualControlReady: true,
    });
    expect(parsed?.flightActivity).toBe('REVISITING');
    expect(parsed?.holdReason).toBe('REVISIT_ARRIVAL');
    expect(parsed?.activeRevisitCandidateId).toBe('cand-4');
    expect(parsed?.manualControlReady).toBe(true);
  });

  it('reads a backend that has none of them without failing', () => {
    // A Phase 1 backend must keep working. Losing the Phase 2 actions is fine;
    // blanking the mission panel is not.
    const parsed = parseExecution(EXECUTION);
    expect(parsed).not.toBeNull();
    expect(parsed?.missionState).toBe('RUNNING');
    expect(parsed?.flightActivity).toBeNull();
    expect(parsed?.holdReason).toBeNull();
    expect(parsed?.activeRevisitCandidateId).toBeNull();
    expect(parsed?.manualControlReady).toBe(false);
  });

  it('ignores values it does not recognize rather than rejecting the execution', () => {
    const parsed = parseExecution({
      ...EXECUTION,
      flightActivity: 'ORBITING',
      holdReason: 'SOMETHING_NEW',
    });
    expect(parsed?.missionState).toBe('RUNNING');
    expect(parsed?.flightActivity).toBeNull();
    expect(parsed?.holdReason).toBeNull();
  });

  it('treats anything other than true as not ready for manual flight', () => {
    for (const value of ['true', 1, {}, null, undefined]) {
      expect(parseExecution({ ...EXECUTION, manualControlReady: value })?.manualControlReady).toBe(false);
    }
  });

  it('treats an empty revisit id as no revisit', () => {
    expect(parseExecution({ ...EXECUTION, activeRevisitCandidateId: '' })?.activeRevisitCandidateId).toBeNull();
  });
});

describe('Phase 2 routes', () => {
  function capture(body: unknown = EXECUTION, status = 200) {
    const fetchImpl = vi.fn().mockResolvedValue(jsonResponse(body, status));
    return { fetchImpl, call: () => fetchImpl.mock.calls[0] as [string, RequestInit] };
  }

  it('posts take-controls to the execution', async () => {
    const { fetchImpl, call } = capture();
    await takeControls('exec-1', { fetchImpl });
    const [url, init] = call();
    expect(url).toBe('/api/missions/exec-1/take-controls');
    expect(init.method).toBe('POST');
    expect(init.body).toBeUndefined();
  });

  it('posts give-controls to the execution', async () => {
    const { fetchImpl, call } = capture();
    await giveControls('exec-1', { fetchImpl });
    expect(call()[0]).toBe('/api/missions/exec-1/give-controls');
  });

  it('posts cancel-revisit to the execution', async () => {
    const { fetchImpl, call } = capture();
    await cancelRevisit('exec-1', { fetchImpl });
    expect(call()[0]).toBe('/api/missions/exec-1/cancel-revisit');
  });

  it('sends only the candidate id when revisiting', async () => {
    // The destination is the backend's stored capture pose. Sending a coordinate
    // from here would let the console steer the aircraft to a point it inferred.
    const { fetchImpl, call } = capture();
    await revisitDetection('exec-1', 'cand-4', { fetchImpl });
    const [url, init] = call();
    expect(url).toBe('/api/missions/exec-1/revisit');
    expect(JSON.parse(init.body as string)).toEqual({ candidateId: 'cand-4' });
  });

  it('carries no latitude or longitude on any Phase 2 request', async () => {
    for (const run of [
      (f: typeof fetch) => takeControls('exec-1', { fetchImpl: f }),
      (f: typeof fetch) => giveControls('exec-1', { fetchImpl: f }),
      (f: typeof fetch) => cancelRevisit('exec-1', { fetchImpl: f }),
      (f: typeof fetch) => revisitDetection('exec-1', 'cand-4', { fetchImpl: f }),
    ]) {
      const { fetchImpl, call } = capture();
      await run(fetchImpl as unknown as typeof fetch);
      const body = String(call()[1].body ?? '');
      expect(body).not.toMatch(/latitude|longitude|lat|lng/i);
    }
  });

  it('escapes ids in the path', async () => {
    const { fetchImpl, call } = capture();
    await revisitDetection('exec/1', 'cand 4', { fetchImpl });
    expect(call()[0]).toBe('/api/missions/exec%2F1/revisit');
    expect(JSON.parse(call()[1].body as string)).toEqual({ candidateId: 'cand 4' });
  });

  it('reuses the shared 409 envelope', async () => {
    const { fetchImpl } = capture(
      { detail: { code: 'MISSION_ACTION_NOT_ALLOWED', message: 'Not while returning home.', details: { state: 'RETURNING_HOME' } } },
      409,
    );
    const result = await takeControls('exec-1', { fetchImpl });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.code).toBe('MISSION_ACTION_NOT_ALLOWED');
      expect(result.error.message).toBe('Not while returning home.');
      expect(result.error.details).toEqual({ state: 'RETURNING_HOME' });
    }
  });

  it('returns the updated execution with its activity fields', async () => {
    const { fetchImpl } = capture({
      ...EXECUTION,
      missionState: 'PAUSED',
      flightActivity: 'MANUAL_CONTROL',
      manualControlReady: true,
    });
    const result = await takeControls('exec-1', { fetchImpl });
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.execution.flightActivity).toBe('MANUAL_CONTROL');
      expect(result.execution.manualControlReady).toBe(true);
    }
  });
});
