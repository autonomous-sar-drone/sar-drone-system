/**
 * Frontend copy of the camera and detector health contract.
 * Source of truth: backend/app/api/schemas/vision.py on
 * feature/backend-camera-feed-person-detection.
 * If the backend schema changes, change it here in the same PR.
 *
 * Note: unlike telemetry_update and mission_update, the backend serializes
 * vision_status WITHOUT exclude_none, so nullable fields arrive as explicit
 * nulls rather than absent keys. The parser accepts both anyway.
 */

/** Reported independently for the camera source and the detector. */
export type HealthState = 'STARTING' | 'LIVE' | 'UNAVAILABLE';

export const HEALTH_STATES: readonly HealthState[] = ['STARTING', 'LIVE', 'UNAVAILABLE'] as const;

export interface CameraHealth {
  state: HealthState;
  /** ISO-8601 UTC time the backend last received a frame, or null before the first. */
  lastFrameAt: string | null;
}

export interface DetectorHealth {
  state: HealthState;
  /** Null until the detector slice lands; the backend never reports fake inference. */
  lastInferenceAt: string | null;
}

export interface VisionStatus {
  sessionId: string;
  /** Increments on every status change. Lets a late message be discarded. */
  revision: number;
  timestamp: string;
  camera: CameraHealth;
  detector: DetectorHealth;
  /** How long the camera has been down, or null while it is healthy. */
  cameraOutageSeconds: number | null;
  /** True once a camera outage has forced an automatic return to home. */
  automaticRtlTriggered: boolean;
}

export interface VisionStatusMessage {
  type: 'vision_status';
  data: VisionStatus;
}
