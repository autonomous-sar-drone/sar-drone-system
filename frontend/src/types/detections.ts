/**
 * Frontend copy of the detection candidate contract.
 * Source of truth: backend/app/api/schemas/detections.py and
 * backend/app/api/routes/detections.py on feature/backend-camera-feed-person-detection.
 * If the backend schema changes, change it here in the same PR.
 *
 * Like vision_status, this event is serialized WITHOUT exclude_none, so nullable
 * fields arrive as explicit nulls. The parser accepts null and absent both.
 */

export type CandidateStatus = 'PENDING' | 'CONFIRMED' | 'DISMISSED';
export const CANDIDATE_STATUSES: readonly CandidateStatus[] = [
  'PENDING',
  'CONFIRMED',
  'DISMISSED',
] as const;

/** How the ground position was worked out. Drives how much the operator should trust it. */
export type LocationMethod =
  | 'FLAT_GROUND_APPROXIMATION'
  | 'TERRAIN_RAYCAST'
  | 'CAMERA_POSITION_FALLBACK';
export const LOCATION_METHODS: readonly LocationMethod[] = [
  'FLAT_GROUND_APPROXIMATION',
  'TERRAIN_RAYCAST',
  'CAMERA_POSITION_FALLBACK',
] as const;

export type LocationQuality = 'APPROXIMATE' | 'TERRAIN_ADJUSTED' | 'CAMERA_POSITION_ONLY';
export const LOCATION_QUALITIES: readonly LocationQuality[] = [
  'APPROXIMATE',
  'TERRAIN_ADJUSTED',
  'CAMERA_POSITION_ONLY',
] as const;

/** Where the person sits in the captured frame, in pixels. */
export interface PixelBoundingBox {
  xPx: number;
  yPx: number;
  widthPx: number;
  heightPx: number;
  frameWidthPx: number;
  frameHeightPx: number;
}

export interface EstimatedPersonLocation {
  latitudeDeg: number;
  longitudeDeg: number;
  method: LocationMethod;
  quality: LocationQuality;
}

export interface DetectionCandidate {
  candidateId: string;
  /** Operator-facing counter, stable per backend session. */
  candidateNumber: number;
  /** Null when the candidate was seen outside a mission execution. */
  executionId: string | null;
  status: CandidateStatus;
  firstObservedAt: string;
  capturedAt: string;
  publishedAt: string;
  /** Null until the operator confirms or dismisses it. */
  reviewedAt: string | null;
  /** 0 to 1 inclusive. */
  confidence: number;
  boundingBox: PixelBoundingBox;
  /** Null when the backend could not work out a ground position. */
  estimatedLocation: EstimatedPersonLocation | null;
  /** Backend path for the frozen evidence snapshot. */
  snapshotUrl: string;
}

/** GET /api/detections */
export interface DetectionCollection {
  sessionId: string;
  revision: number;
  candidates: DetectionCandidate[];
}

/** One candidate appearing or changing. Never carries the whole collection. */
export interface DetectionUpdateData {
  sessionId: string;
  revision: number;
  candidate: DetectionCandidate;
}

export interface DetectionUpdateMessage {
  type: 'detection_update';
  data: DetectionUpdateData;
}

export type ReviewDecision = 'CONFIRM' | 'DISMISS';

export type DetectionErrorCode =
  | 'CANDIDATE_NOT_FOUND'
  | 'CANDIDATE_ALREADY_REVIEWED'
  | 'NETWORK_ERROR'
  | 'UNEXPECTED_RESPONSE';

export interface DetectionError {
  code: DetectionErrorCode | (string & {});
  message: string;
  details: Record<string, unknown>;
}
