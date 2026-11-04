import type { Feature } from 'geojson';
import type { DetectionCandidate } from '../../types/detections';

/**
 * Which detections belong on the map, and how each one is drawn.
 *
 * Two candidates are deliberately left off:
 *
 * - DISMISSED. The operator has already looked and said it is not a person.
 *   Leaving it on the map competes for attention with the ones that matter.
 *
 * - CAMERA_POSITION_ONLY. That quality means the backend could not work out
 *   where the person is and fell back to reporting where the DRONE was. Drawing
 *   it as a person marker would put a pin somewhere nobody has been seen, and a
 *   rescuer could be sent there. The detections panel still lists it, with the
 *   fallback spelled out in words, so nothing is hidden from the operator; it is
 *   only kept off the one surface that means "a person is here".
 */
export function isMappable(candidate: DetectionCandidate): boolean {
  if (candidate.status === 'DISMISSED') return false;
  const location = candidate.estimatedLocation;
  if (location === null) return false;
  return location.quality !== 'CAMERA_POSITION_ONLY';
}

/**
 * GeoJSON for the detections source. `confirmed` drives the layer filters so a
 * confirmed person reads differently from one still awaiting review.
 */
export function detectionFeatures(candidates: DetectionCandidate[]): Feature[] {
  const features: Feature[] = [];
  for (const candidate of candidates) {
    if (!isMappable(candidate)) continue;
    const location = candidate.estimatedLocation;
    if (location === null) continue; // narrowed by isMappable; kept for the type checker
    features.push({
      type: 'Feature',
      properties: {
        candidateId: candidate.candidateId,
        candidateNumber: candidate.candidateNumber,
        confirmed: candidate.status === 'CONFIRMED',
        // Flat-ground positions are the rougher of the two we plot.
        approximate: location.quality === 'APPROXIMATE',
      },
      geometry: {
        type: 'Point',
        coordinates: [location.longitudeDeg, location.latitudeDeg],
      },
    });
  }
  return features;
}
