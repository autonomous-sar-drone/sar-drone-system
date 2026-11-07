import type { MissionUpdateData } from '../../types/mission';
import type { VisionStatus } from '../../types/vision';

/**
 * What the CAMERA has to say about the aircraft's safety, and nothing else.
 *
 * Mission state is narrated by MissionControls and MISSION_STATE_COPY. This banner
 * only adds what those cannot know: that the camera is the reason, or that a
 * camera-driven return was attempted but never confirmed. Restating mission state
 * here would put two differently worded accounts of the same thing on one screen.
 *
 * The distinction this exists to protect: `automaticRtlTriggered` says the backend
 * ATTEMPTED a return home after a camera outage. It is not an acknowledgement from
 * the flight controller. Only `missionState === 'RETURNING_HOME'` means the aircraft
 * is actually coming back. Telling an operator the drone is returning when the
 * command may have been rejected is the kind of wrong that gets acted on.
 */

export type SafetyTone = 'alert' | 'notice';

export interface SafetyBanner {
  tone: SafetyTone;
  text: string;
}

export function cameraSafetyBanner(
  vision: VisionStatus | null,
  mission: MissionUpdateData | null,
): SafetyBanner | null {
  const state = mission?.missionState ?? null;
  const cameraCaused = mission?.interruptionReason === 'CAMERA_UNAVAILABLE';

  // Confirmed: the controller accepted the return. Keyed on mission state, so the
  // camera coming back afterwards does not cancel the message.
  if (state === 'RETURNING_HOME' && cameraCaused) {
    return {
      tone: 'alert',
      text: 'The camera was lost, so the aircraft is returning home. The search did not finish.',
    };
  }

  // Terminal, and it keeps the backend's reason rather than restating it as a guess.
  if (state === 'INTERRUPTED' && cameraCaused) {
    return {
      tone: 'alert',
      text: 'The mission stopped because the camera was unavailable.',
    };
  }

  // Attempted but unconfirmed: the backend tried to bring it home and the aircraft
  // has not agreed. The runtimeError, when there is one, is the authoritative
  // account of why the attempt did not take.
  if (vision?.automaticRtlTriggered && state !== 'RETURNING_HOME') {
    const detail = mission?.runtimeError?.message;
    return {
      tone: 'alert',
      text: detail
        ? `The camera outage triggered a return home, but the aircraft has not confirmed it: ${detail}`
        : 'The camera outage triggered a return home, but the aircraft has not confirmed it yet. Watch the mission state.',
    };
  }

  // An operator stop is not a camera matter, and a healthy mission needs no banner.
  return null;
}
