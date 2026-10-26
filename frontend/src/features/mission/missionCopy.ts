import type { MissionError, MissionState } from '../../types/mission';

/**
 * Operator-facing wording for each backend mission state. Every message says what the
 * aircraft is doing, in plain language, because the operator is reading this mid-flight.
 */
export const MISSION_STATE_COPY: Record<MissionState, { label: string; detail: string }> = {
  IDLE: { label: 'Not started', detail: 'No mission is running.' },
  PLANNING: { label: 'Planning', detail: 'Working out the route for the search area.' },
  PREFLIGHT_CHECK: {
    label: 'Preparing for takeoff',
    detail: 'Checking the aircraft is ready, then arming and climbing.',
  },
  RUNNING: { label: 'Searching', detail: 'Flying the route and covering the search area.' },
  PAUSED: { label: 'Paused', detail: 'Holding position. Resume to carry on from the next waypoint.' },
  RETURNING_HOME: { label: 'Returning home', detail: 'The search was stopped and the aircraft is flying back.' },
  INTERRUPTED: {
    label: 'Interrupted',
    detail: 'The mission stopped on its own. Check the aircraft, then continue or bring it home.',
  },
  COMPLETED: { label: 'Search complete', detail: 'Every waypoint on the route was reached.' },
};

/** True while an execution exists that the operator can still act on. */
export function isLive(state: MissionState): boolean {
  return (
    state === 'PREFLIGHT_CHECK' ||
    state === 'RUNNING' ||
    state === 'PAUSED' ||
    state === 'RETURNING_HOME' ||
    state === 'INTERRUPTED'
  );
}

interface OperatorError {
  title: string;
  action: string;
}

/**
 * Turns a backend error code into something the operator can act on.
 * Codes come from MissionExecutionErrorCode in app/mission/errors.py.
 */
export function missionErrorMessage(error: MissionError): OperatorError {
  switch (error.code) {
    case 'MISSION_ACTION_NOT_ALLOWED':
      return {
        title: "That isn't allowed right now.",
        action: 'The mission moved on while the request was in flight. Check the state and try again.',
      };
    case 'PLAN_NOT_CURRENT':
      return {
        title: 'This plan is out of date.',
        action: 'The search area changed since it was generated. Generate the route again.',
      };
    case 'EXECUTION_NOT_CURRENT':
      return {
        title: 'This mission is no longer the active one.',
        action: 'Another mission has started since. Refresh to pick up the current one.',
      };
    case 'VEHICLE_POSITION_UNAVAILABLE':
      return {
        title: "The aircraft's position is unknown.",
        action: 'Wait for GPS to settle, then try again.',
      };
    case 'VEHICLE_NOT_READY':
      return {
        title: 'The aircraft is not ready to fly.',
        action: 'Check that it is connected, armed and reporting a heartbeat, then try again.',
      };
    case 'VEHICLE_COMMAND_FAILED':
      return {
        title: 'The aircraft rejected the command.',
        action: 'It did not confirm the change. Check the aircraft before retrying.',
      };
    case 'MISSION_MONITOR_FAILED':
      return {
        title: 'The backend lost track of the mission.',
        action: 'Treat the aircraft as unsupervised. Bring it home and check the backend logs.',
      };
    case 'NETWORK_ERROR':
      return {
        title: 'Could not reach the backend.',
        action: 'Check that it is running, then try again.',
      };
    default:
      return {
        title: error.message || 'The command failed.',
        action: 'Check the backend logs for details.',
      };
  }
}
