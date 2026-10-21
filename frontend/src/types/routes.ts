/**
 * Frontend copy of the POST /api/routes/preview contract.
 * Source of truth: "W06 Frontend Handoff: Search Area + Route Preview", sections 3-5.
 */
import type { LatLon } from '../features/searchArea/geometry';

export interface PlanningSettings {
  searchAltitudeM: number;
  laneSpacingM: number;
}

export interface RoutePreviewRequest {
  /** Ordered vertices. Never closed with a duplicate first vertex; no `closed` flag. */
  searchArea: { vertices: LatLon[] };
  /** Optional. When omitted the backend applies its configured defaults. */
  planning?: PlanningSettings;
}

export type AltitudeReference = 'HOME_RELATIVE';

export interface RouteWaypoint extends LatLon {
  altitudeM: number;
  altitudeReference: AltitudeReference;
}

export interface Route {
  waypoints: RouteWaypoint[];
}

export interface ProposedPlan {
  planId: string;
  route: Route;
  metrics: {
    distanceToStartM: number;
    routeDistanceM: number;
  };
  planningUsed: PlanningSettings;
}

export type PlanErrorCode =
  | 'INVALID_SEARCH_AREA'
  | 'VEHICLE_POSITION_UNAVAILABLE'
  | 'SEARCH_AREA_OUT_OF_RANGE'
  | 'ROUTE_TOO_LONG'
  | 'MISSION_ACTION_NOT_ALLOWED'
  /** Frontend-only codes for failures that never reached the contract. */
  | 'NETWORK_ERROR'
  | 'UNEXPECTED_RESPONSE';

export interface PlanError {
  code: PlanErrorCode | (string & {});
  message: string;
  details: Record<string, unknown>;
}
