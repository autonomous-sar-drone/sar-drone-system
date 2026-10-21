import type { PlanError } from '../../types/routes';

export interface OperatorMessage {
  title: string;
  action: string;
}

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);

function formatDistance(m: number): string {
  return m >= 1000 ? `${(m / 1000).toFixed(1)} km` : `${Math.round(m)} m`;
}

const SHAPE_REASON: Record<string, string> = {
  SELF_INTERSECTION: 'Its edges cross each other.',
  TOO_FEW_VERTICES: 'It needs at least three corners.',
  DEGENERATE_AREA: 'It has almost no area.',
  AREA_TOO_LARGE: 'It is larger than the drone can search in one mission.',
};

/** Turns a backend error code into something an operator can act on. */
export function operatorMessage(error: PlanError): OperatorMessage {
  const d = error.details;
  switch (error.code) {
    case 'INVALID_SEARCH_AREA': {
      const reason = typeof d.reason === 'string' ? SHAPE_REASON[d.reason] : undefined;
      return {
        title: `The backend can't use this search area. ${reason ?? error.message}`.trim(),
        action: 'Adjust the corners and generate again.',
      };
    }
    case 'VEHICLE_POSITION_UNAVAILABLE':
      return {
        title: "The backend doesn't have a current drone position, so it can't check the route.",
        action: 'Check that telemetry is live, then generate again.',
      };
    case 'SEARCH_AREA_OUT_OF_RANGE': {
      const dist = num(d.distanceToStartM);
      const max = num(d.maxTransitDistanceM);
      const detail =
        dist !== null && max !== null
          ? ` The route starts ${formatDistance(dist)} away; the limit is ${formatDistance(max)}.`
          : '';
      return {
        title: `The search area is too far from the drone.${detail}`,
        action: 'Draw the area closer to the drone.',
      };
    }
    case 'ROUTE_TOO_LONG':
      return {
        title: 'The search route would be longer than the mission allows.',
        action: 'Make the search area smaller.',
      };
    case 'MISSION_ACTION_NOT_ALLOWED':
      return {
        title: "Planning isn't allowed in the mission's current state.",
        action: error.message || 'Wait for the current mission to finish.',
      };
    case 'NETWORK_ERROR':
      return {
        title: "Couldn't reach the backend.",
        action: 'Check that the backend is running, then try again.',
      };
    default:
      return {
        title: error.message || 'The backend rejected the plan.',
        action: `Error code: ${error.code}`,
      };
  }
}
