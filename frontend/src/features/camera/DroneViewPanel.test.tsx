import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DroneViewPanel, MAX_AUTO_RETRIES } from './DroneViewPanel';
import { outageText } from './cameraCopy';
import type { MissionState, MissionUpdateData } from '../../types/mission';
import type { HealthState, VisionStatus } from '../../types/vision';

function status(cameraState: HealthState, extra: Partial<VisionStatus> = {}): VisionStatus {
  return {
    sessionId: 'vision-session-abc123',
    revision: 1,
    timestamp: '2026-11-07T15:00:00.000Z',
    camera: { state: cameraState, lastFrameAt: '2026-11-07T15:00:00.000Z' },
    detector: { state: 'UNAVAILABLE', lastInferenceAt: null },
    cameraOutageSeconds: null,
    automaticRtlTriggered: false,
    ...extra,
  };
}

function mission(missionState: MissionState, extra: Partial<MissionUpdateData> = {}): MissionUpdateData {
  return {
    executionId: 'exec-1',
    planId: 'plan-1',
    missionState,
    activeTarget: null,
    interruptionReason: null,
    runtimeError: null,
    timestamp: '2026-11-07T15:00:00.000Z',
    ...extra,
  };
}

const stream = () => screen.queryByRole('img', { name: /live view from the drone camera/i });

beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
afterEach(() => vi.useRealTimers());

describe('DroneViewPanel feed', () => {
  it('shows the stream only while the backend reports the camera live', () => {
    const { rerender } = render(<DroneViewPanel vision={status('STARTING')} mission={null} />);
    expect(stream()).toBeNull();
    expect(screen.getByText(/Connecting to the camera/)).toBeInTheDocument();

    rerender(<DroneViewPanel vision={status('LIVE')} mission={null} />);
    expect(stream()).toBeInTheDocument();

    // A frozen last frame must not keep looking live once frames stop arriving.
    rerender(<DroneViewPanel vision={status('UNAVAILABLE')} mission={null} />);
    expect(stream()).toBeNull();
    expect(screen.getByText(/not receiving frames/)).toBeInTheDocument();
  });

  it('requests a new stream URL each time the camera comes back', () => {
    const { rerender } = render(<DroneViewPanel vision={status('LIVE')} mission={null} />);
    const first = stream()?.getAttribute('src');
    rerender(<DroneViewPanel vision={status('UNAVAILABLE')} mission={null} />);
    rerender(<DroneViewPanel vision={status('LIVE')} mission={null} />);
    expect(stream()?.getAttribute('src')).not.toBe(first);
  });

  it('retries a dropped browser stream on a new URL without blaming the camera', () => {
    render(<DroneViewPanel vision={status('LIVE')} mission={null} />);
    const before = stream()?.getAttribute('src');

    act(() => void fireEvent.error(stream()!));
    // Bounded, not immediate: no rapid retry loop against the endpoint.
    expect(stream()?.getAttribute('src')).toBe(before);
    act(() => void vi.advanceTimersByTime(2000));

    expect(stream()).toBeInTheDocument();
    expect(stream()?.getAttribute('src')).not.toBe(before);
    expect(screen.queryByText(/No camera feed/)).toBeNull();
  });

  it('hands over to a manual retry once the automatic attempts are spent', () => {
    render(<DroneViewPanel vision={status('LIVE')} mission={null} />);

    for (let i = 0; i < MAX_AUTO_RETRIES; i += 1) {
      act(() => void fireEvent.error(stream()!));
      act(() => void vi.advanceTimersByTime(2000));
    }
    act(() => void fireEvent.error(stream()!));

    expect(stream()).toBeNull();
    // The camera is fine; only this browser's connection failed, and the copy says so.
    expect(screen.getByText(/Video stream dropped in this browser/)).toBeInTheDocument();
    expect(screen.getByText(/camera is still running/i)).toBeInTheDocument();
    expect(screen.getByText('Camera').nextSibling).toHaveTextContent('Live');

    fireEvent.click(screen.getByRole('button', { name: 'Retry feed' }));
    expect(stream()).toBeInTheDocument();
  });

  it('keeps the unavailable message when the camera source really is down', () => {
    render(<DroneViewPanel vision={status('UNAVAILABLE', { cameraOutageSeconds: 42 })} mission={null} />);
    expect(screen.getByText(/No camera feed/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Retry feed' })).toBeNull();
    expect(screen.getByText('Camera').nextSibling).toHaveTextContent('Down for 42s');
  });

  it('reports a detector that does not exist yet', () => {
    render(<DroneViewPanel vision={status('LIVE')} mission={null} />);
    expect(screen.getByText('Detector').nextSibling).toHaveTextContent('Not running');
  });

  it('says it is waiting before any status has arrived', () => {
    render(<DroneViewPanel vision={null} mission={null} />);
    expect(screen.getByText(/Waiting for the backend/)).toBeInTheDocument();
  });
});

describe('DroneViewPanel safety messaging', () => {
  it('does not say the aircraft is returning when the return is only attempted', () => {
    render(
      <DroneViewPanel
        vision={status('UNAVAILABLE', { automaticRtlTriggered: true })}
        mission={mission('RUNNING')}
      />,
    );
    const banner = screen.getByRole('status');
    expect(banner).toHaveTextContent(/has not confirmed/);
    expect(banner).not.toHaveTextContent(/is returning home\./);
  });

  it('confirms the camera-loss return once the mission state agrees', () => {
    render(
      <DroneViewPanel
        vision={status('UNAVAILABLE', { automaticRtlTriggered: true })}
        mission={mission('RETURNING_HOME', { interruptionReason: 'CAMERA_UNAVAILABLE' })}
      />,
    );
    expect(screen.getByRole('status')).toHaveTextContent(
      /camera was lost, so the aircraft is returning home/i,
    );
  });

  it('says nothing about a stop the operator asked for', () => {
    // Not a camera matter; the mission controls already narrate it.
    render(
      <DroneViewPanel
        vision={status('LIVE')}
        mission={mission('RETURNING_HOME', { interruptionReason: 'OPERATOR_STOP' })}
      />,
    );
    expect(screen.queryByRole('status')).toBeNull();
  });
});

describe('outageText', () => {
  it('stays quiet under a second and rounds down after that', () => {
    expect(outageText(null)).toBeNull();
    expect(outageText(0.4)).toBeNull();
    expect(outageText(7.9)).toBe('Down for 7s');
    expect(outageText(60)).toBe('Down for 1m');
    expect(outageText(135)).toBe('Down for 2m 15s');
  });
});
