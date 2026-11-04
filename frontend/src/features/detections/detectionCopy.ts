import type {
  CandidateStatus,
  DetectionError,
  LocationQuality,
} from '../../types/detections';

export const STATUS_COPY: Record<CandidateStatus, string> = {
  PENDING: 'Needs review',
  CONFIRMED: 'Confirmed',
  DISMISSED: 'Dismissed',
};

/**
 * How much the operator should trust the position. This drives a real decision,
 * since someone may be sent to these coordinates.
 */
export const QUALITY_COPY: Record<LocationQuality, string> = {
  TERRAIN_ADJUSTED: 'Adjusted for terrain',
  APPROXIMATE: 'Approximate, assumes flat ground',
  CAMERA_POSITION_ONLY: "Drone's own position, not the person's",
};

export function confidenceText(confidence: number): string {
  return `${Math.round(confidence * 100)}% confidence`;
}

export function coordinateText(latitudeDeg: number, longitudeDeg: number): string {
  return `${latitudeDeg.toFixed(5)}, ${longitudeDeg.toFixed(5)}`;
}

/** Short relative time, so a row reads "2m ago" rather than a full timestamp. */
export function relativeTime(iso: string, nowMs: number): string {
  const thenMs = Date.parse(iso);
  if (Number.isNaN(thenMs)) return '';
  const seconds = Math.max(0, Math.round((nowMs - thenMs) / 1000));
  if (seconds < 10) return 'just now';
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ago`;
}

export function detectionErrorMessage(error: DetectionError): { title: string; action: string } {
  switch (error.code) {
    case 'CANDIDATE_NOT_FOUND':
      return {
        title: 'That detection is no longer available.',
        action: 'The backend session restarted, so the list has been reloaded.',
      };
    case 'CANDIDATE_ALREADY_REVIEWED':
      return {
        title: 'That detection was already reviewed.',
        action: 'The list has been refreshed with the decision that was recorded.',
      };
    case 'NETWORK_ERROR':
      return {
        title: 'Could not reach the backend.',
        action: 'Check that it is running, then try again.',
      };
    default:
      return {
        title: error.message || 'The review failed.',
        action: 'Check the backend logs for details.',
      };
  }
}
