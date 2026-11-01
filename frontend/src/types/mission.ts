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

/** Body of every /api/missions/* response. */
export interface MissionExecution {
  executionId: string;
  planId: string;
  missionState: MissionState;
  /** Absent in the payload when there is no current target; normalized to null here. */
  activeTarget: ActiveTarget | null;
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
export type MissionCommand = 'commence' | 'pause' | 'resume' | 'stop' | 'continue';
