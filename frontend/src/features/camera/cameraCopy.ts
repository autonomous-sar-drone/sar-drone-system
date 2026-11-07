import type { HealthState, VisionStatus } from '../../types/vision';

/** Path the backend serves the MJPEG stream from. The dev server proxies /api. */
export const CAMERA_STREAM_URL = '/api/camera/stream.mjpg';

/** Terse labels for the status rows. The headline above carries the full sentence. */
export const CAMERA_STATE_COPY: Record<HealthState, string> = {
  STARTING: 'Starting',
  LIVE: 'Live',
  UNAVAILABLE: 'No feed',
};

export const DETECTOR_STATE_COPY: Record<HealthState, string> = {
  STARTING: 'Starting',
  LIVE: 'Running',
  UNAVAILABLE: 'Not running',
};

/** Whole seconds read better than three decimal places on a status line. */
export function outageText(seconds: number | null): string | null {
  if (seconds === null || seconds < 1) return null;
  const whole = Math.floor(seconds);
  if (whole < 60) return `Down for ${whole}s`;
  const minutes = Math.floor(whole / 60);
  const rest = whole % 60;
  return rest === 0 ? `Down for ${minutes}m` : `Down for ${minutes}m ${rest}s`;
}

/**
 * What the operator should understand about the FEED right now.
 *
 * Only the feed. Anything about the aircraft returning home belongs to
 * cameraSafetyBanner, which can see mission state; automaticRtlTriggered on its own
 * is an attempted return, not a confirmed one, so this must not speak for it.
 */
export function cameraMessage(vision: VisionStatus | null): { title: string; detail: string } {
  if (vision === null) {
    return {
      title: 'Waiting for the backend',
      detail: 'The camera feed appears here once the backend reports its status.',
    };
  }
  switch (vision.camera.state) {
    case 'LIVE':
      return { title: 'Live', detail: '' };
    case 'STARTING':
      return {
        title: 'Connecting to the camera…',
        detail: 'Waiting for the first frame from the simulator.',
      };
    case 'UNAVAILABLE':
      return {
        title: 'No camera feed',
        detail:
          'The backend is not receiving frames. Check that the simulator is running and ' +
          'the camera publisher is connected.',
      };
  }
}
