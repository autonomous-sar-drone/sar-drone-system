/**
 * Client for the /api/missions routes.
 * Source of truth: backend/app/api/routes/missions.py.
 *
 * Every route returns the same MissionExecution body, and every mission failure comes
 * back as HTTP 409 with the shared { detail: { code, message, details } } envelope,
 * so one parser covers all five commands.
 */
import type {
  ActiveTarget,
  FlightActivity,
  HoldReason,
  InterruptionReason,
  MissionError,
  MissionExecution,
  MissionState,
} from '../types/mission';
import {
  FLIGHT_ACTIVITIES,
  HOLD_REASONS,
  INTERRUPTION_REASONS,
  MISSION_STATES,
} from '../types/mission';

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

/**
 * PHASE 2 fields, read leniently on purpose.
 *
 * An absent field means a Phase 1 backend and an unrecognized value means a backend
 * newer than this build. Both resolve to null rather than failing the whole
 * execution: losing Phase 2 actions is recoverable, but rejecting the payload would
 * blank the mission panel and take the existing controls down with it.
 */
export function parseFlightActivity(raw: unknown): FlightActivity | null {
  return typeof raw === 'string' && FLIGHT_ACTIVITIES.includes(raw as FlightActivity)
    ? (raw as FlightActivity)
    : null;
}

export function parseHoldReason(raw: unknown): HoldReason | null {
  return typeof raw === 'string' && HOLD_REASONS.includes(raw as HoldReason)
    ? (raw as HoldReason)
    : null;
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
    // Same field as on mission_update, so an HTTP response and a socket snapshot
    // of the same execution never disagree about why it was interrupted.
    interruptionReason:
      typeof raw.interruptionReason === 'string' &&
      INTERRUPTION_REASONS.includes(raw.interruptionReason as InterruptionReason)
        ? (raw.interruptionReason as InterruptionReason)
        : null,
    flightActivity: parseFlightActivity(raw.flightActivity),
    holdReason: parseHoldReason(raw.holdReason),
    activeRevisitCandidateId:
      typeof raw.activeRevisitCandidateId === 'string' && raw.activeRevisitCandidateId !== ''
        ? raw.activeRevisitCandidateId
        : null,
    // Anything other than an explicit true is treated as not ready. Manual flight is
    // the one action that moves the aircraft by hand, so it stays off by default.
    manualControlReady: raw.manualControlReady === true,
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

/*
 * PHASE 2 routes (PROPOSED; mockable). Same MissionExecution body and the same
 * 409 { detail: { code, message, details } } envelope as every route above, so
 * they reuse post() and need no error handling of their own.
 */

/** Request manual ownership. The backend decides; readiness arrives on the next update. */
export function takeControls(
  executionId: string,
  options: CallOptions = {},
): Promise<MissionResult> {
  return post(`/api/missions/${encodeURIComponent(executionId)}/take-controls`, undefined, options);
}

/** Hand control back. Neutral hover / HOLDING only; never resumes the search. */
export function giveControls(
  executionId: string,
  options: CallOptions = {},
): Promise<MissionResult> {
  return post(`/api/missions/${encodeURIComponent(executionId)}/give-controls`, undefined, options);
}

/**
 * Fly back to the stored capture viewpoint for one candidate.
 *
 * Sends the candidate id and nothing else, on purpose. The destination is the
 * historical DRONE/CAMERA pose the backend recorded at capture time, which is not
 * the same place as the estimated person location, and is not what the map marker
 * sits on. Sending coordinates from here would let the console steer the aircraft
 * to a point it inferred, which is exactly the failure the fallback rule exists to
 * prevent.
 */
export function revisitDetection(
  executionId: string,
  candidateId: string,
  options: CallOptions = {},
): Promise<MissionResult> {
  return post(
    `/api/missions/${encodeURIComponent(executionId)}/revisit`,
    { candidateId },
    options,
  );
}

/** Stop a revisit relocation and hold. Never automatically resumes the search. */
export function cancelRevisit(
  executionId: string,
  options: CallOptions = {},
): Promise<MissionResult> {
  return post(
    `/api/missions/${encodeURIComponent(executionId)}/cancel-revisit`,
    undefined,
    options,
  );
}
