import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { DetectionsPanel } from './DetectionsPanel';
import type { DetectionsState } from './useDetections';
import type { DetectionCandidate } from '../../types/detections';

function candidate(overrides: Partial<DetectionCandidate> = {}): DetectionCandidate {
  return {
    candidateId: 'cand-1',
    candidateNumber: 1,
    executionId: 'exec-1',
    status: 'PENDING',
    firstObservedAt: '2026-11-04T15:00:00.000Z',
    capturedAt: '2026-11-04T15:00:00.000Z',
    publishedAt: '2026-11-04T15:00:00.000Z',
    reviewedAt: null,
    confidence: 0.82,
    boundingBox: {
      xPx: 1, yPx: 1, widthPx: 10, heightPx: 10, frameWidthPx: 640, frameHeightPx: 480,
    },
    estimatedLocation: {
      latitudeDeg: 30.2291, longitudeDeg: -97.7555,
      method: 'TERRAIN_RAYCAST', quality: 'TERRAIN_ADJUSTED',
    },
    snapshotUrl: '/api/detections/cand-1/snapshot',
    ...overrides,
  };
}

const NOW = Date.parse('2026-11-04T15:02:00.000Z');

function state(overrides: Partial<DetectionsState> = {}): DetectionsState {
  return {
    candidates: [],
    loading: false,
    error: null,
    reviewing: null,
    reviewError: null,
    review: vi.fn(),
    dismissReviewError: vi.fn(),
    ...overrides,
  };
}

describe('DetectionsPanel', () => {
  it('explains the empty case rather than showing a blank panel', () => {
    render(<DetectionsPanel detections={state()} nowMs={NOW} />);
    expect(screen.getByText(/No detections yet/)).toBeInTheDocument();
  });

  it('shows the snapshot, confidence and position quality', () => {
    render(<DetectionsPanel detections={state({ candidates: [candidate()] })} nowMs={NOW} />);
    expect(screen.getByRole('img', { name: /Snapshot of possible person 1/ })).toBeInTheDocument();
    expect(screen.getByText(/82% confidence/)).toBeInTheDocument();
    expect(screen.getByText(/30\.22910, -97\.75550/)).toBeInTheDocument();
    expect(screen.getByText(/Adjusted for terrain/)).toBeInTheDocument();
  });

  it('says plainly when the position is only the drone, not the person', () => {
    const location = {
      latitudeDeg: 30.1, longitudeDeg: -97.7,
      method: 'CAMERA_POSITION_FALLBACK' as const, quality: 'CAMERA_POSITION_ONLY' as const,
    };
    render(
      <DetectionsPanel
        detections={state({ candidates: [candidate({ estimatedLocation: location })] })}
        nowMs={NOW}
      />,
    );
    expect(screen.getByText(/Drone's own position, not the person's/)).toBeInTheDocument();
  });

  it('does not leave a missing position looking like it is still loading', () => {
    render(
      <DetectionsPanel
        detections={state({ candidates: [candidate({ estimatedLocation: null })] })}
        nowMs={NOW}
      />,
    );
    expect(screen.getByText(/No ground position could be worked out/)).toBeInTheDocument();
  });

  it('offers review buttons only while a candidate is pending', () => {
    const { rerender } = render(
      <DetectionsPanel detections={state({ candidates: [candidate()] })} nowMs={NOW} />,
    );
    expect(screen.getByRole('button', { name: 'Confirm' })).toBeEnabled();

    rerender(
      <DetectionsPanel
        detections={state({ candidates: [candidate({ status: 'CONFIRMED' })] })}
        nowMs={NOW}
      />,
    );
    expect(screen.queryByRole('button', { name: 'Confirm' })).toBeNull();
  });

  it('sends the decision for the row that was clicked', () => {
    const review = vi.fn();
    render(
      <DetectionsPanel
        detections={state({ candidates: [candidate({ candidateId: 'cand-7' })], review })}
        nowMs={NOW}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(review).toHaveBeenCalledWith('cand-7', 'DISMISS');
  });

  it('locks every row while one review is in flight', () => {
    render(
      <DetectionsPanel
        detections={state({
          candidates: [candidate(), candidate({ candidateId: 'cand-2', candidateNumber: 2 })],
          reviewing: 'cand-1',
        })}
        nowMs={NOW}
      />,
    );
    for (const button of screen.getAllByRole('button', { name: 'Confirm' })) {
      expect(button).toBeDisabled();
    }
  });

  it('counts only the candidates still needing review', () => {
    render(
      <DetectionsPanel
        detections={state({
          candidates: [candidate(), candidate({ candidateId: 'c2', status: 'DISMISSED' })],
        })}
        nowMs={NOW}
      />,
    );
    expect(screen.getByText('1 to review')).toBeInTheDocument();
  });

  it('surfaces a failed review with a way to clear it', () => {
    const dismissReviewError = vi.fn();
    render(
      <DetectionsPanel
        detections={state({
          candidates: [candidate()],
          reviewError: { code: 'NETWORK_ERROR', message: 'nope', details: {} },
          dismissReviewError,
        })}
        nowMs={NOW}
      />,
    );
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('Could not reach the backend');
    fireEvent.click(within(alert).getByRole('button', { name: 'Dismiss' }));
    expect(dismissReviewError).toHaveBeenCalled();
  });
});
