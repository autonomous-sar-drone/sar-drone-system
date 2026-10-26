import { describe, expect, it, vi } from 'vitest';
import {
  commenceMission,
  continueMission,
  parseExecution,
  parseMissionError,
  pauseMission,
  resumeMission,
  stopMission,
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
