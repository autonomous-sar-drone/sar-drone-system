import { describe, expect, it } from 'vitest';
import { cameraSafetyBanner } from './cameraSafety';
import type { InterruptionReason, MissionState, MissionUpdateData } from '../../types/mission';
import type { HealthState, VisionStatus } from '../../types/vision';

function vision(
  cameraState: HealthState,
  automaticRtlTriggered = false,
): VisionStatus {
  return {
    sessionId: 's1',
    revision: 1,
    timestamp: '2026-11-07T15:00:00.000Z',
    camera: { state: cameraState, lastFrameAt: '2026-11-07T15:00:00.000Z' },
    detector: { state: 'LIVE', lastInferenceAt: '2026-11-07T15:00:00.000Z' },
    cameraOutageSeconds: cameraState === 'UNAVAILABLE' ? 12 : null,
    automaticRtlTriggered,
  };
}

function mission(
  missionState: MissionState,
  interruptionReason: InterruptionReason | null = null,
  runtimeError: MissionUpdateData['runtimeError'] = null,
): MissionUpdateData {
  return {
    executionId: 'exec-1',
    planId: 'plan-1',
    missionState,
    activeTarget: null,
    interruptionReason,
    runtimeError,
    timestamp: '2026-11-07T15:00:00.000Z',
  };
}

describe('cameraSafetyBanner', () => {
  it('leaves mission-state narration to the mission controls', () => {
    // MISSION_STATE_COPY already says what INTERRUPTED and RETURNING_HOME mean.
    // Two differently worded accounts of one event is worse than one.
    expect(cameraSafetyBanner(vision('LIVE'), mission('INTERRUPTED'))).toBeNull();
    expect(cameraSafetyBanner(vision('LIVE'), mission('RETURNING_HOME', 'OPERATOR_STOP'))).toBeNull();
    expect(cameraSafetyBanner(vision('LIVE'), mission('INTERRUPTED', 'OPERATOR_STOP'))).toBeNull();
  });

  it('keeps the reason on a camera-caused interruption', () => {
    const banner = cameraSafetyBanner(
      vision('UNAVAILABLE', true),
      mission('INTERRUPTED', 'CAMERA_UNAVAILABLE'),
    );
    expect(banner?.text).toMatch(/stopped because the camera was unavailable/i);
  });

  it('does not claim the aircraft is returning when only an attempt was made', () => {
    // automaticRtlTriggered is the backend TRYING. The controller has not agreed.
    const banner = cameraSafetyBanner(vision('UNAVAILABLE', true), mission('RUNNING'));
    expect(banner?.text).not.toMatch(/is returning home/);
    expect(banner?.text).toMatch(/has not confirmed/);
    expect(banner?.tone).toBe('alert');
  });

  it('quotes the backend runtime error when the attempt failed', () => {
    const banner = cameraSafetyBanner(
      vision('UNAVAILABLE', true),
      mission('RUNNING', null, { code: 'VEHICLE_COMMAND_FAILED', message: 'RTL rejected', details: {} }),
    );
    expect(banner?.text).toMatch(/RTL rejected/);
    expect(banner?.text).not.toMatch(/is returning home/);
  });

  it('confirms a camera-loss return only once the mission state says so', () => {
    const banner = cameraSafetyBanner(
      vision('UNAVAILABLE', true),
      mission('RETURNING_HOME', 'CAMERA_UNAVAILABLE'),
    );
    expect(banner?.text).toMatch(/camera was lost, so the aircraft is returning home/i);
    expect(banner?.tone).toBe('alert');
  });

  it('keeps reporting the return after the camera comes back', () => {
    // Camera recovery does not undo an RTL the controller already accepted.
    const banner = cameraSafetyBanner(
      vision('LIVE', true),
      mission('RETURNING_HOME', 'CAMERA_UNAVAILABLE'),
    );
    expect(banner?.text).toMatch(/returning home/i);
  });

  
  
  
  it('says nothing while a healthy mission runs', () => {
    expect(cameraSafetyBanner(vision('LIVE'), mission('RUNNING'))).toBeNull();
    expect(cameraSafetyBanner(null, null)).toBeNull();
  });

  it('says nothing for a camera outage the backend has not acted on', () => {
    // A short outage with no RTL attempt is reported by the camera row, not as an alert.
    expect(cameraSafetyBanner(vision('UNAVAILABLE', false), mission('RUNNING'))).toBeNull();
  });
});
