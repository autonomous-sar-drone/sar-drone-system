/** Every message type /ws/events can deliver that this build understands. */
import type { MissionUpdateMessage } from './mission';
import type { TelemetryUpdateMessage } from './telemetry';

/**
 * coverage_update and detection_update join this union when those slices land.
 * parseEvent reports anything else as unknown_type rather than failing.
 */
export type EventMessage = TelemetryUpdateMessage | MissionUpdateMessage;
