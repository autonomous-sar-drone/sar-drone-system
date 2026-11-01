/**
 * Client for the /api/missions routes.
 * Source of truth: backend/app/api/routes/missions.py.
 *
 * Every route returns the same MissionExecution body, and every mission failure comes
 * back as HTTP 409 with the shared { detail: { code, message, details } } envelope,
 * so one parser covers all five commands.
 */
import type { ActiveTarget, MissionError, MissionExecution, MissionState } from '../types/mission';
import { MISSION_STATES } from '../types/mission';

export type MissionResult =
  | { ok: true; execution: MissionExecution }
  | { ok: false; error: MissionError };

const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null;

function parseActiveTarget(raw: unknown): ActiveTarget | null {
  if (!isObj(raw)) return null;
  if (!isNum(raw.latitudeDeg) || !isNum(raw.longitudeDeg) || !isNum(raw.altitudeM)) return null;
  if (raw.altitudeReference !== 'HOME_RELATIVE') return null;
  return {
    latitudeDeg: raw.latitudeDeg,
    longitudeDeg: raw.longitudeDeg,
    altitudeM: raw.altitudeM,
    altitudeReference: 'HOME_RELATIVE',
  };
}

export function parseExecution(raw: unknown): MissionExecution | null {
  if (!isObj(raw)) return null;
  if (typeof raw.executionId !== 'string' || typeof raw.planId !== 'string') return null;
  if (typeof raw.missionState !== 'string') return null;
  if (!MISSION_STATES.includes(raw.missionState as MissionState)) return null;
  return {
    executionId: raw.executionId,
    planId: raw.planId,
    missionState: raw.missionState as MissionState,
    // Serialized with exclude_none, so an absent target means "no current target".
    activeTarget: raw.activeTarget == null ? null : parseActiveTarget(raw.activeTarget),
  };
}

/** Reads the shared { detail: { code, message, details } } error shape. */
export function parseMissionError(status: number, raw: unknown): MissionError {
  const detail = isObj(raw) ? raw.detail : undefined;
  if (isObj(detail) && typeof detail.code === 'string') {
    return {
      code: detail.code,
      message: typeof detail.message === 'string' ? detail.message : '',
      details: isObj(detail.details) ? detail.details : {},
    };
  }
  return {
    code: 'UNEXPECTED_RESPONSE',
    message: `The backend returned an unexpected response (HTTP ${status}).`,
    details: { status, body: raw },
  };
}

interface CallOptions {
  signal?: AbortSignal;
  fetchImpl?: typeof fetch;
}

async function post(path: string, body: unknown, options: CallOptions): Promise<MissionResult> {
  const fetchImpl = options.fetchImpl ?? fetch;
  let response: Response;
  try {
    response = await fetchImpl(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: options.signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') throw err;
    return {
      ok: false,
      error: { code: 'NETWORK_ERROR', message: 'Could not reach the backend.', details: {} },
    };
  }

  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    /* non-JSON body; handled below */
  }

  if (response.ok) {
    const execution = parseExecution(payload);
    if (execution) return { ok: true, execution };
    return {
      ok: false,
      error: {
        code: 'UNEXPECTED_RESPONSE',
        message: 'The backend returned a mission in an unexpected format.',
        details: { body: payload },
      },
    };
  }
  return { ok: false, error: parseMissionError(response.status, payload) };
}

/** Start flying a plan the backend has already validated. */
export function commenceMission(planId: string, options: CallOptions = {}): Promise<MissionResult> {
  return post('/api/missions/commence', { planId }, options);
}

export function pauseMission(executionId: string, options: CallOptions = {}): Promise<MissionResult> {
  return post(`/api/missions/${encodeURIComponent(executionId)}/pause`, undefined, options);
}

export function resumeMission(executionId: string, options: CallOptions = {}): Promise<MissionResult> {
  return post(`/api/missions/${encodeURIComponent(executionId)}/resume`, undefined, options);
}

/** Abort the search and return home. */
export function stopMission(executionId: string, options: CallOptions = {}): Promise<MissionResult> {
  return post(`/api/missions/${encodeURIComponent(executionId)}/stop`, undefined, options);
}

/** Recover an INTERRUPTED execution. Not a new plan and not a new execution id. */
export function continueMission(
  executionId: string,
  options: CallOptions = {},
): Promise<MissionResult> {
  return post(`/api/missions/${encodeURIComponent(executionId)}/continue`, undefined, options);
}
