/**
 * Frontend copy of the mission execution contract.
 * Source of truth: backend/app/api/schemas/mission_execution.py and
 * backend/app/api/routes/missions.py on feature/backend-mission-execution.
 * If the backend schema changes, change it here in the same PR.
 */
import type { LatLon } from '../features/searchArea/geometry';
import type { AltitudeReference } from './routes';

/**
 * Mission lifecycle, owned by the backend. The frontend never computes this,
 * it only reflects whatever the last mission_update reported.
 * Mirrors app/domain/models/mission_state.py.
 */
export type MissionState =
  | 'IDLE'
  | 'PLANNING'
  | 'PREFLIGHT_CHECK'
  | 'RUNNING'
  | 'PAUSED'
  | 'RETURNING_HOME'
  | 'INTERRUPTED'
  | 'COMPLETED';

export const MISSION_STATES: readonly MissionState[] = [
  'IDLE',
  'PLANNING',
  'PREFLIGHT_CHECK',
  'RUNNING',
  'PAUSED',
  'RETURNING_HOME',
  'INTERRUPTED',
  'COMPLETED',
] as const;

/** The waypoint the vehicle is currently flying toward. */
export interface ActiveTarget extends LatLon {
  altitudeM: number;
  altitudeReference: AltitudeReference;
}

/**
 * Why the backend interrupted the mission itself. Mirrors InterruptionReason in
 * app/domain/models/mission_execution.py. Null when nothing interrupted it.
 */
export type InterruptionReason = 'CAMERA_UNAVAILABLE' | 'OPERATOR_STOP';

export const INTERRUPTION_REASONS: readonly InterruptionReason[] = [
  'CAMERA_UNAVAILABLE',
  'OPERATOR_STOP',
] as const;

/**
 * PHASE 2 (PROPOSED CONTRACT, awaiting backend sign-off).
 *
 * What the aircraft is doing *within* a mission state, as opposed to the lifecycle
 * state itself. Deliberately a separate server-owned field rather than extra
 * MissionState members: PAUSED still means paused whether the operator is flying by
 * hand, holding at a viewpoint, or rejoining the route, and widening MissionState
 * would break every existing consumer of it.
 *
 * Null when the backend has not sent one, which is how a Phase 1 backend reads.
 */
export type FlightActivity =
  | 'SEARCHING'
  | 'HOLDING'
  | 'MANUAL_CONTROL'
  | 'PREPARING_REJOIN'
  | 'REJOINING'
  | 'REVISITING';

export const FLIGHT_ACTIVITIES: readonly FlightActivity[] = [
  'SEARCHING',
  'HOLDING',
  'MANUAL_CONTROL',
  'PREPARING_REJOIN',
  'REJOINING',
  'REVISITING',
] as const;

/** PHASE 2. Why the aircraft is holding, when it is. Null when it is not holding. */
export type HoldReason = 'OPERATOR_PAUSE' | 'MANUAL_RELEASE' | 'REVISIT_ARRIVAL';

export const HOLD_REASONS: readonly HoldReason[] = [
  'OPERATOR_PAUSE',
  'MANUAL_RELEASE',
  'REVISIT_ARRIVAL',
] as const;

/** Body of every /api/missions/* response. */
export interface MissionExecution {
  executionId: string;
  planId: string;
  missionState: MissionState;
  /** Absent in the payload when there is no current target; normalized to null here. */
  activeTarget: ActiveTarget | null;
  /**
   * The backend's own explanation for an interruption or return. Kept because it is
   * the only authoritative account of WHY the aircraft is coming back; the frontend
   * must never substitute a guess for it.
   */
  interruptionReason: InterruptionReason | null;
  /**
   * PHASE 2, server-owned. Null when the backend does not send it (Phase 1) or has
   * no activity to report. No click may optimistically set this: takeover, holding
   * and arrival are only ever true once the backend says so.
   */
  flightActivity: FlightActivity | null;
  /** PHASE 2, server-owned. Null unless the aircraft is holding. */
  holdReason: HoldReason | null;
  /** PHASE 2. The candidate currently being revisited, or null. */
  activeRevisitCandidateId: string | null;
  /**
   * PHASE 2. Whether the backend will accept manual input right now. Defaults to
   * false when absent, so a Phase 1 backend disables manual flight rather than
   * letting the console offer a control channel that does not exist.
   */
  manualControlReady: boolean;
}

/**
 * A guidance failure the backend hit on its own, outside any request we made
 * (lost heartbeat, unexpected disarm). Only ever arrives on the WebSocket.
 */
export interface MissionRuntimeError {
  code: string;
  message: string;
  details: Record<string, unknown>;
}

/** mission_update adds a timestamp and background errors on top of the HTTP body. */
export interface MissionUpdateData extends MissionExecution {
  /** ISO-8601 UTC time the backend produced this snapshot. */
  timestamp: string;
  runtimeError: MissionRuntimeError | null;
}

export interface MissionUpdateMessage {
  type: 'mission_update';
  data: MissionUpdateData;
}

/**
 * Codes from app/mission/errors.py MissionExecutionErrorCode, returned as HTTP 409
 * and echoed in runtimeError. Widened so an unrecognized code still renders.
 */
export type MissionErrorCode =
  | 'MISSION_ACTION_NOT_ALLOWED'
  | 'PLAN_NOT_CURRENT'
  | 'EXECUTION_NOT_CURRENT'
  | 'VEHICLE_POSITION_UNAVAILABLE'
  | 'VEHICLE_NOT_READY'
  | 'VEHICLE_COMMAND_FAILED'
  | 'MISSION_MONITOR_FAILED'
  /** Frontend-only codes for failures that never reached the contract. */
  | 'NETWORK_ERROR'
  | 'UNEXPECTED_RESPONSE';

export interface MissionError {
  code: MissionErrorCode | (string & {});
  message: string;
  details: Record<string, unknown>;
}

/** Mission commands the operator can issue. Matches the route suffixes. */
export type MissionCommand =
  | 'commence'
  | 'pause'
  | 'resume'
  | 'stop'
  | 'continue'
  /** PHASE 2 route suffixes. */
  | 'take-controls'
  | 'give-controls'
  | 'revisit'
  | 'cancel-revisit';
