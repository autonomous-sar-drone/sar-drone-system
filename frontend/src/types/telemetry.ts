/**
 * Frontend copy of the frozen /ws/events contract.
 * Source of truth: docs "Technical Design and Initial Implementation Plan", section 2.3.
 * If the backend schema changes, change it here in the same PR.
 */

/** Payload of a telemetry_update message. All units are explicit in the field names. */
export interface TelemetryData {
  /** ISO-8601 UTC time the backend produced this sample. */
  timestamp: string;
  latitudeDeg: number;
  longitudeDeg: number;
  /** Meters above the home / takeoff point, not above sea level. */
  relativeAltitudeM: number;
  /** Degrees 0-360. null when the vehicle reports heading as unknown. */
  headingDeg: number | null;
  groundSpeedMps: number;
}

export interface TelemetryUpdateMessage {
  type: 'telemetry_update';
  data: TelemetryData;
}

/**
 * Every message type the frontend currently understands.
 * mission_update, coverage_update and detection_update join this union
 * when those slices are implemented.
 */
export type EventMessage = TelemetryUpdateMessage;

/** A telemetry sample plus what the browser knows about when it arrived. */
export interface TelemetryReading {
  data: TelemetryData;
  /** Browser clock (ms since epoch) when the message was received. */
  receivedAtMs: number;
  /**
   * receivedAtMs minus the backend timestamp. Measures backend-to-browser delay,
   * not drone-to-browser delay. null if the timestamp could not be parsed.
   */
  latencyMs: number | null;
}
