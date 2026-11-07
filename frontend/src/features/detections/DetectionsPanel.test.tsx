import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { DetectionsPanel } from './DetectionsPanel';
import { pendingQueue, resolveSelection } from './reviewQueue';
import type { DetectionsState } from './useDetections';
import type { CandidateStatus, DetectionCandidate, LocationQuality } from '../../types/detections';

function candidate(
  candidateNumber: number,
  status: CandidateStatus = 'PENDING',
  quality: LocationQuality | null = 'TERRAIN_ADJUSTED',
): DetectionCandidate {
  return {
    candidateId: `cand-${candidateNumber}`,
    candidateNumber,
    executionId: 'exec-1',
    status,
    firstObservedAt: '2026-11-07T15:00:00.000Z',
    capturedAt: '2026-11-07T15:00:00.000Z',
    publishedAt: '2026-11-07T15:00:00.000Z',
    reviewedAt: null,
    confidence: 0.82,
    boundingBox: {
      xPx: 1, yPx: 1, widthPx: 10, heightPx: 10, frameWidthPx: 640, frameHeightPx: 480,
    },
    estimatedLocation: quality
      ? {
          latitudeDeg: 30.2291,
          longitudeDeg: -97.7555,
          method: quality === 'CAMERA_POSITION_ONLY' ? 'CAMERA_POSITION_FALLBACK' : 'TERRAIN_RAYCAST',
          quality,
        }
      : null,
    snapshotUrl: `/api/detections/cand-${candidateNumber}/snapshot`,
  };
}

const NOW = Date.parse('2026-11-07T15:02:00.000Z');

/** Derives pending/selected the way the real hook does, so the stub cannot drift. */
function state(
  candidates: DetectionCandidate[],
  overrides: Partial<DetectionsState> = {},
): DetectionsState {
  const pending = pendingQueue(candidates);
  const selectedId = resolveSelection(pending, overrides.selectedId ?? null);
  return {
    candidates,
    pending,
    selectedId,
    selected: pending.find((c) => c.candidateId === selectedId) ?? null,
    loading: false,
    stale: false,
    error: null,
    reviewing: null,
    reviewError: null,
    select: vi.fn(),
    selectNeighbour: vi.fn(),
    canSelectNeighbour: () => true,
    review: vi.fn(),
    dismissReviewError: vi.fn(),
    ...overrides,
  };
}

function renderPanel(detections: DetectionsState, onShowOnMap = vi.fn()) {
  render(<DetectionsPanel detections={detections} nowMs={NOW} onShowOnMap={onShowOnMap} />);
  return { onShowOnMap };
}

const cardNames = () =>
  screen.getAllByRole('button', { name: /Possible person \d+/ }).map((b) => b.getAttribute('aria-label'));

describe('DetectionsPanel queue', () => {
  it('shows pending candidates chronologically, oldest first', () => {
    renderPanel(state([candidate(3), candidate(1), candidate(2)]));
    expect(cardNames()).toEqual([
      'Possible person 1',
      'Possible person 2',
      'Possible person 3',
    ]);
  });

  it('leaves reviewed candidates out of the queue', () => {
    renderPanel(state([candidate(1), candidate(2, 'CONFIRMED'), candidate(3, 'DISMISSED')]));
    expect(cardNames()).toEqual(['Possible person 1']);
    expect(screen.getByText('1 to review')).toBeInTheDocument();
  });

  it('says the queue is empty even while confirmed records are still held', () => {
    // Those records stay in memory for the map; the operator still has nothing to do.
    renderPanel(state([candidate(1, 'CONFIRMED'), candidate(2, 'DISMISSED')]));
    expect(screen.getByText(/No pending detections/)).toBeInTheDocument();
    expect(screen.queryByText(/to review/)).toBeNull();
  });

  it('marks the selected card and shows its detail', () => {
    renderPanel(state([candidate(1), candidate(2)], { selectedId: 'cand-2' }));
    const selected = screen.getByRole('button', { name: 'Possible person 2' });
    expect(selected).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('img', { name: /Snapshot of possible person 2/ })).toBeInTheDocument();
    expect(screen.getByText(/82% confidence/)).toBeInTheDocument();
  });

  it('selects a card without reviewing it', () => {
    const select = vi.fn();
    const review = vi.fn();
    renderPanel(state([candidate(1), candidate(2)], { select, review }));
    fireEvent.click(screen.getByRole('button', { name: 'Possible person 2' }));
    expect(select).toHaveBeenCalledWith('cand-2');
    expect(review).not.toHaveBeenCalled();
  });

  it('navigates the queue without recording a decision', () => {
    const selectNeighbour = vi.fn();
    const review = vi.fn();
    renderPanel(state([candidate(1), candidate(2)], { selectNeighbour, review }));
    fireEvent.click(screen.getByRole('button', { name: 'Later' }));
    expect(selectNeighbour).toHaveBeenCalledWith('next');
    fireEvent.click(screen.getByRole('button', { name: 'Earlier' }));
    expect(selectNeighbour).toHaveBeenCalledWith('previous');
    expect(review).not.toHaveBeenCalled();
  });

  it('disables navigation at the end the queue cannot pass', () => {
    renderPanel(
      state([candidate(1), candidate(2)], { canSelectNeighbour: (d) => d !== 'next' }),
    );
    expect(screen.getByRole('button', { name: 'Later' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Earlier' })).toBeEnabled();
  });
});

describe('DetectionsPanel review actions', () => {
  it('reviews the selected candidate', () => {
    const review = vi.fn();
    renderPanel(state([candidate(1), candidate(2)], { selectedId: 'cand-2', review }));
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }));
    expect(review).toHaveBeenCalledWith('cand-2', 'CONFIRM');
  });

  it('locks the actions while a review is in flight', () => {
    renderPanel(state([candidate(1)], { reviewing: 'cand-1' }));
    expect(screen.getByRole('button', { name: 'Confirm' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Dismiss' })).toBeDisabled();
  });

  it('keeps the card and snapshot after a failed review', () => {
    renderPanel(
      state([candidate(1)], {
        reviewError: { code: 'NETWORK_ERROR', message: 'nope', details: {} },
      }),
    );
    expect(screen.getByRole('img', { name: /Snapshot of possible person 1/ })).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('Could not reach the backend');
  });

  it('lets a failed review be dismissed', () => {
    const dismissReviewError = vi.fn();
    renderPanel(
      state([candidate(1)], {
        reviewError: { code: 'NETWORK_ERROR', message: 'nope', details: {} },
        dismissReviewError,
      }),
    );
    fireEvent.click(within(screen.getByRole('alert')).getByRole('button', { name: 'Dismiss' }));
    expect(dismissReviewError).toHaveBeenCalled();
  });
});

describe('DetectionsPanel show on map', () => {
  it('asks the map to centre on the selected candidate', () => {
    const { onShowOnMap } = renderPanel(state([candidate(1)]));
    fireEvent.click(screen.getByRole('button', { name: 'Show on map' }));
    expect(onShowOnMap).toHaveBeenCalledWith(expect.objectContaining({ candidateId: 'cand-1' }));
  });

  it('cannot be used when there is no position to centre on', () => {
    renderPanel(state([candidate(1, 'PENDING', null)]));
    expect(screen.getByRole('button', { name: 'Show on map' })).toBeDisabled();
    expect(screen.getByText(/No ground position could be worked out/)).toBeInTheDocument();
  });

  it('labels a camera-position fallback as the drone, not the person', () => {
    // Navigating there must never read as "the person is here".
    renderPanel(state([candidate(1, 'PENDING', 'CAMERA_POSITION_ONLY')]));
    expect(screen.getByRole('button', { name: 'Show drone position' })).toBeEnabled();
    expect(
      screen.getByText(/where the drone was when it saw something, not where the person is/),
    ).toBeInTheDocument();
  });
});

describe('DetectionsPanel connection state', () => {
  it('says the list may be out of date while disconnected', () => {
    renderPanel(state([candidate(1)], { stale: true }));
    expect(screen.getByRole('status')).toHaveTextContent(/may be out of date/);
  });

  it('says nothing about staleness while connected', () => {
    renderPanel(state([candidate(1)]));
    expect(screen.queryByRole('status')).toBeNull();
  });
});
