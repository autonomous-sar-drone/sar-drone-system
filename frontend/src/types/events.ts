/** Every message type /ws/events can deliver that this build understands. */
import type { DetectionUpdateMessage } from './detections';
import type { MissionUpdateMessage } from './mission';
import type { TelemetryUpdateMessage } from './telemetry';
import type { VisionStatusMessage } from './vision';

/**
 * coverage_update joins this union when that slice lands.
 * parseEvent reports anything else as unknown_type rather than failing.
 */
export type EventMessage =
  | TelemetryUpdateMessage
  | MissionUpdateMessage
  | VisionStatusMessage
  | DetectionUpdateMessage;
