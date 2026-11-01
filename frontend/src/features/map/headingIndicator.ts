import type { LatLon } from '../searchArea/geometry';

/** Drone position plus the heading from the same telemetry_update sample. */
export interface DronePose extends LatLon {
  /** Degrees clockwise from true north. null when the aircraft reports heading as unknown. */
  headingDeg: number | null;
}

/**
 * Length of the heading stick drawn ahead of the drone marker.
 * Geographic (not pixel) length, so it scales with zoom like the route and search-area lines.
 */
export const HEADING_SEGMENT_M = 25;

const M_PER_DEG_LAT = 111_320;
/** Below this the longitude scale blows up near the poles; clamp rather than divide by ~0. */
const MIN_COS_LAT = 1e-6;

/**
 * Short segment from the drone position toward its reported heading.
 *
 * Returns null when heading is unknown, so the caller draws the plain marker instead of
 * pointing the operator in an arbitrary direction (ST-014: heading is shown as unknown,
 * never as a misleading value).
 */
export function headingSegment(
  pose: DronePose,
  lengthM: number = HEADING_SEGMENT_M,
): [LatLon, LatLon] | null {
  if (pose.headingDeg === null || !Number.isFinite(pose.headingDeg)) return null;
  if (!Number.isFinite(lengthM) || lengthM <= 0) return null;

  // Compass bearing: 0 = north, 90 = east, measured clockwise.
  const bearingDeg = ((pose.headingDeg % 360) + 360) % 360;
  const bearingRad = (bearingDeg * Math.PI) / 180;

  const northM = lengthM * Math.cos(bearingRad);
  const eastM = lengthM * Math.sin(bearingRad);

  const cosLat = Math.max(Math.abs(Math.cos((pose.latitudeDeg * Math.PI) / 180)), MIN_COS_LAT);

  return [
    { latitudeDeg: pose.latitudeDeg, longitudeDeg: pose.longitudeDeg },
    {
      latitudeDeg: pose.latitudeDeg + northM / M_PER_DEG_LAT,
      longitudeDeg: pose.longitudeDeg + eastM / (M_PER_DEG_LAT * cosLat),
    },
  ];
}
