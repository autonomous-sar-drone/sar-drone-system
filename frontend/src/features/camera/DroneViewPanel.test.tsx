import { render, screen } from '@testing-library/react';
import { fireEvent } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { DroneViewPanel } from './DroneViewPanel';
import { outageText } from './cameraCopy';
import type { HealthState, VisionStatus } from '../../types/vision';

function status(
  cameraState: HealthState,
  extra: Partial<VisionStatus> = {},
): VisionStatus {
  return {
    sessionId: 'vision-session-abc123',
    revision: 1,
    timestamp: '2026-11-02T20:00:00.000Z',
    camera: { state: cameraState, lastFrameAt: '2026-11-02T20:00:00.000Z' },
    detector: { state: 'UNAVAILABLE', lastInferenceAt: null },
    cameraOutageSeconds: null,
    automaticRtlTriggered: false,
    ...extra,
  };
}

const stream = () => screen.queryByRole('img', { name: /live view from the drone camera/i });

describe('DroneViewPanel', () => {
  it('shows the stream only while the backend reports the camera live', () => {
    const { rerender } = render(<DroneViewPanel vision={status('STARTING')} />);
    expect(stream()).toBeNull();
    expect(screen.getByText(/Connecting to the camera/)).toBeInTheDocument();

    rerender(<DroneViewPanel vision={status('LIVE')} />);
    expect(stream()).toBeInTheDocument();

    // A frozen last frame must not keep looking live once frames stop arriving.
    rerender(<DroneViewPanel vision={status('UNAVAILABLE')} />);
    expect(stream()).toBeNull();
    expect(screen.getByText(/not receiving frames/)).toBeInTheDocument();
  });

  it('requests a new stream URL each time the camera comes back', () => {
    const { rerender } = render(<DroneViewPanel vision={status('LIVE')} />);
    const first = stream()?.getAttribute('src');

    rerender(<DroneViewPanel vision={status('UNAVAILABLE')} />);
    rerender(<DroneViewPanel vision={status('LIVE')} />);
    const second = stream()?.getAttribute('src');

    expect(first).toBeTruthy();
    expect(second).toBeTruthy();
    // Reusing the URL would let the browser hold the dead connection open.
    expect(second).not.toBe(first);
  });

  it('stops claiming to be live if the stream itself fails to load', () => {
    render(<DroneViewPanel vision={status('LIVE')} />);
    const img = stream();
    expect(img).toBeInTheDocument();

    fireEvent.error(img!);
    expect(stream()).toBeNull();
    expect(screen.getByText(/Camera stream dropped/)).toBeInTheDocument();
  });

  it('leads with the automatic return home over the camera state', () => {
    render(
      <DroneViewPanel
        vision={status('UNAVAILABLE', { automaticRtlTriggered: true, cameraOutageSeconds: 42 })}
      />,
    );
    expect(screen.getByText(/Camera lost, returning home/)).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('sent the aircraft home automatically');
  });

  it('reports both health rows, including a detector that does not exist yet', () => {
    render(<DroneViewPanel vision={status('LIVE', { cameraOutageSeconds: null })} />);
    expect(screen.getByText('Camera').nextSibling).toHaveTextContent('Live');
    expect(screen.getByText('Detector').nextSibling).toHaveTextContent('Not running');
  });

  it('says it is waiting before any status has arrived', () => {
    render(<DroneViewPanel vision={null} />);
    expect(stream()).toBeNull();
    expect(screen.getByText(/Waiting for the backend/)).toBeInTheDocument();
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
