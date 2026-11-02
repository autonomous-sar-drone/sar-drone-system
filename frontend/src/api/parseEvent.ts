import type { EventMessage } from '../types/events';
import {
  MISSION_STATES,
  type ActiveTarget,
  type MissionRuntimeError,
  type MissionState,
  type MissionUpdateData,
} from '../types/mission';
import type { TelemetryData } from '../types/telemetry';
import {
  HEALTH_STATES,
  type CameraHealth,
  type DetectorHealth,
  type HealthState,
  type VisionStatus,
} from '../types/vision';

export type ParseResult =
  | { kind: 'ok'; message: EventMessage }
  /** Well-formed message of a type this build does not handle yet. Safe to ignore. */
  | { kind: 'unknown_type'; type: string }
  | { kind: 'invalid'; reason: string };

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function isObj(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function parseTelemetryData(raw: unknown): TelemetryData | string {
  if (typeof raw !== 'object' || raw === null) return 'data is not an object';
  const d = raw as Record<string, unknown>;

  if (typeof d.timestamp !== 'string' || Number.isNaN(Date.parse(d.timestamp))) {
    return 'timestamp is missing or not ISO-8601';
  }
  for (const field of ['latitudeDeg', 'longitudeDeg', 'relativeAltitudeM', 'groundSpeedMps'] as const) {
    if (!isFiniteNumber(d[field])) return `${field} is missing or not a finite number`;
  }
  if (d.headingDeg !== null && !isFiniteNumber(d.headingDeg)) {
    return 'headingDeg must be a finite number or null';
  }
  if (Math.abs(d.latitudeDeg as number) > 90) return 'latitudeDeg is out of range';
  if (Math.abs(d.longitudeDeg as number) > 180) return 'longitudeDeg is out of range';

  return {
    timestamp: d.timestamp,
    latitudeDeg: d.latitudeDeg as number,
    longitudeDeg: d.longitudeDeg as number,
    relativeAltitudeM: d.relativeAltitudeM as number,
    headingDeg: d.headingDeg as number | null,
    groundSpeedMps: d.groundSpeedMps as number,
  };
}

function parseActiveTarget(raw: unknown): ActiveTarget | string {
  if (!isObj(raw)) return 'activeTarget is not an object';
  for (const field of ['latitudeDeg', 'longitudeDeg', 'altitudeM'] as const) {
    if (!isFiniteNumber(raw[field])) return `activeTarget.${field} is missing or not a number`;
  }
  // Only HOME_RELATIVE exists today. Rejecting anything else stops an altitude being
  // silently read against the wrong datum.
  if (raw.altitudeReference !== 'HOME_RELATIVE') {
    return 'activeTarget.altitudeReference must be HOME_RELATIVE';
  }
  return {
    latitudeDeg: raw.latitudeDeg as number,
    longitudeDeg: raw.longitudeDeg as number,
    altitudeM: raw.altitudeM as number,
    altitudeReference: 'HOME_RELATIVE',
  };
}

function parseRuntimeError(raw: unknown): MissionRuntimeError | string {
  if (!isObj(raw)) return 'runtimeError is not an object';
  if (typeof raw.code !== 'string') return 'runtimeError.code is missing';
  if (typeof raw.message !== 'string') return 'runtimeError.message is missing';
  return {
    code: raw.code,
    message: raw.message,
    details: isObj(raw.details) ? raw.details : {},
  };
}

function parseMissionData(raw: unknown): MissionUpdateData | string {
  if (!isObj(raw)) return 'data is not an object';
  const d = raw;

  for (const field of ['executionId', 'planId'] as const) {
    if (typeof d[field] !== 'string') return `${field} is missing or not a string`;
  }
  if (typeof d.timestamp !== 'string' || Number.isNaN(Date.parse(d.timestamp))) {
    return 'timestamp is missing or not ISO-8601';
  }
  if (typeof d.missionState !== 'string' || !MISSION_STATES.includes(d.missionState as MissionState)) {
    return `missionState is missing or unrecognized: ${String(d.missionState)}`;
  }

  // The backend serializes with exclude_none, so these arrive absent rather than null.
  let activeTarget: ActiveTarget | null = null;
  if (d.activeTarget !== undefined && d.activeTarget !== null) {
    const parsed = parseActiveTarget(d.activeTarget);
    if (typeof parsed === 'string') return parsed;
    activeTarget = parsed;
  }

  let runtimeError: MissionRuntimeError | null = null;
  if (d.runtimeError !== undefined && d.runtimeError !== null) {
    const parsed = parseRuntimeError(d.runtimeError);
    if (typeof parsed === 'string') return parsed;
    runtimeError = parsed;
  }

  return {
    executionId: d.executionId as string,
    planId: d.planId as string,
    missionState: d.missionState as MissionState,
    activeTarget,
    runtimeError,
    timestamp: d.timestamp,
  };
}

function parseHealth(raw: unknown, name: 'camera' | 'detector', timeField: string):
  | { state: HealthState; at: string | null }
  | string {
  if (!isObj(raw)) return `${name} is missing or not an object`;
  if (typeof raw.state !== 'string' || !HEALTH_STATES.includes(raw.state as HealthState)) {
    return `${name}.state is missing or unrecognized: ${String(raw.state)}`;
  }

  // Null before the first frame or inference. Absent is tolerated too, in case the
  // backend ever switches this event to exclude_none like the other two.
  const rawAt = raw[timeField];
  let at: string | null = null;
  if (rawAt !== undefined && rawAt !== null) {
    if (typeof rawAt !== 'string' || Number.isNaN(Date.parse(rawAt))) {
      return `${name}.${timeField} must be ISO-8601 or null`;
    }
    at = rawAt;
  }

  return { state: raw.state as HealthState, at };
}

function parseVisionStatus(raw: unknown): VisionStatus | string {
  if (!isObj(raw)) return 'data is not an object';
  const d = raw;

  if (typeof d.sessionId !== 'string') return 'sessionId is missing or not a string';
  if (!isFiniteNumber(d.revision) || d.revision < 0) return 'revision is missing or negative';
  if (typeof d.timestamp !== 'string' || Number.isNaN(Date.parse(d.timestamp))) {
    return 'timestamp is missing or not ISO-8601';
  }
  if (typeof d.automaticRtlTriggered !== 'boolean') {
    return 'automaticRtlTriggered is missing or not a boolean';
  }

  const camera = parseHealth(d.camera, 'camera', 'lastFrameAt');
  if (typeof camera === 'string') return camera;
  const detector = parseHealth(d.detector, 'detector', 'lastInferenceAt');
  if (typeof detector === 'string') return detector;

  // Null while the camera is healthy; a number only during an outage.
  let cameraOutageSeconds: number | null = null;
  if (d.cameraOutageSeconds !== undefined && d.cameraOutageSeconds !== null) {
    if (!isFiniteNumber(d.cameraOutageSeconds)) return 'cameraOutageSeconds must be a number or null';
    cameraOutageSeconds = d.cameraOutageSeconds;
  }

  return {
    sessionId: d.sessionId,
    revision: d.revision,
    timestamp: d.timestamp,
    camera: { state: camera.state, lastFrameAt: camera.at } satisfies CameraHealth,
    detector: { state: detector.state, lastInferenceAt: detector.at } satisfies DetectorHealth,
    cameraOutageSeconds,
    automaticRtlTriggered: d.automaticRtlTriggered,
  };
}

/** Parse one raw WebSocket frame. Never throws. */
export function parseEvent(raw: string): ParseResult {
  let json: unknown;
  try {
    json = JSON.parse(raw);
  } catch {
    return { kind: 'invalid', reason: 'frame is not valid JSON' };
  }
  if (typeof json !== 'object' || json === null) {
    return { kind: 'invalid', reason: 'frame is not a JSON object' };
  }
  const msg = json as Record<string, unknown>;
  if (typeof msg.type !== 'string') {
    return { kind: 'invalid', reason: 'message has no string "type" discriminator' };
  }

  switch (msg.type) {
    case 'telemetry_update': {
      const data = parseTelemetryData(msg.data);
      if (typeof data === 'string') return { kind: 'invalid', reason: `telemetry_update: ${data}` };
      return { kind: 'ok', message: { type: 'telemetry_update', data } };
    }
    case 'mission_update': {
      const data = parseMissionData(msg.data);
      if (typeof data === 'string') return { kind: 'invalid', reason: `mission_update: ${data}` };
      return { kind: 'ok', message: { type: 'mission_update', data } };
    }
    case 'vision_status': {
      const data = parseVisionStatus(msg.data);
      if (typeof data === 'string') return { kind: 'invalid', reason: `vision_status: ${data}` };
      return { kind: 'ok', message: { type: 'vision_status', data } };
    }
    default:
      return { kind: 'unknown_type', type: msg.type };
  }
}
