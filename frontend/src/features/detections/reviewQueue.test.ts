import { describe, expect, it } from 'vitest';
import {
  neighbourId,
  pendingQueue,
  resolveSelection,
  selectionAfterReview,
  sortByNumber,
} from './reviewQueue';
import type { CandidateStatus, DetectionCandidate } from '../../types/detections';

function candidate(candidateNumber: number, status: CandidateStatus = 'PENDING'): DetectionCandidate {
  return {
    candidateId: `cand-${candidateNumber}`,
    candidateNumber,
    executionId: 'exec-1',
    status,
    firstObservedAt: '2026-11-07T15:00:00.000Z',
    capturedAt: '2026-11-07T15:00:00.000Z',
    publishedAt: '2026-11-07T15:00:00.000Z',
    reviewedAt: null,
    confidence: 0.8,
    boundingBox: {
      xPx: 1, yPx: 1, widthPx: 10, heightPx: 10, frameWidthPx: 640, frameHeightPx: 480,
    },
    estimatedLocation: null,
    snapshotUrl: `/api/detections/cand-${candidateNumber}/snapshot`,
    ...{},
  };
}

const ids = (list: DetectionCandidate[]) => list.map((c) => c.candidateId);

describe('pendingQueue', () => {
  it('keeps only pending candidates, oldest first', () => {
    const queue = pendingQueue([
      candidate(3),
      candidate(1),
      candidate(2, 'CONFIRMED'),
      candidate(4, 'DISMISSED'),
    ]);
    expect(ids(queue)).toEqual(['cand-1', 'cand-3']);
  });

  it('is empty when every candidate has been reviewed', () => {
    // Confirmed records stay in the authoritative collection for the map, but the
    // review queue is still empty and the panel must say so.
    expect(pendingQueue([candidate(1, 'CONFIRMED'), candidate(2, 'DISMISSED')])).toEqual([]);
  });
});

describe('sortByNumber', () => {
  it('orders chronologically by the backend counter', () => {
    expect(ids(sortByNumber([candidate(3), candidate(1), candidate(2)])))
      .toEqual(['cand-1', 'cand-2', 'cand-3']);
  });
});

describe('resolveSelection', () => {
  const queue = [candidate(1), candidate(2), candidate(3)];

  it('defaults to the oldest waiting candidate', () => {
    expect(resolveSelection(queue, null)).toBe('cand-1');
  });

  it('keeps the current selection when a new candidate arrives', () => {
    // Selecting #2 and then receiving #4 must not move the operator off #2.
    const withArrival = [...queue, candidate(4)];
    expect(resolveSelection(withArrival, 'cand-2')).toBe('cand-2');
  });

  it('falls back to the oldest when the selection has left the queue', () => {
    expect(resolveSelection([candidate(2), candidate(3)], 'cand-1')).toBe('cand-2');
  });

  it('is null when nothing is waiting', () => {
    expect(resolveSelection([], 'cand-1')).toBeNull();
  });
});

describe('neighbourId', () => {
  const queue = [candidate(1), candidate(2), candidate(3)];

  it('moves later and earlier through the queue', () => {
    expect(neighbourId(queue, 'cand-2', 'next')).toBe('cand-3');
    expect(neighbourId(queue, 'cand-2', 'previous')).toBe('cand-1');
  });

  it('does not wrap at either end', () => {
    // Wrapping would silently re-show a card the operator already passed over.
    expect(neighbourId(queue, 'cand-3', 'next')).toBeNull();
    expect(neighbourId(queue, 'cand-1', 'previous')).toBeNull();
  });

  it('falls back to the oldest when the current selection is unknown', () => {
    expect(neighbourId(queue, null, 'next')).toBe('cand-1');
    expect(neighbourId(queue, 'gone', 'next')).toBe('cand-1');
  });
});

describe('selectionAfterReview', () => {
  const queue = [candidate(1), candidate(2), candidate(3)];

  it('advances to the next card on the right', () => {
    expect(selectionAfterReview(queue, 'cand-2')).toBe('cand-3');
  });

  it('falls back to the nearest card on the left at the newest end', () => {
    expect(selectionAfterReview(queue, 'cand-3')).toBe('cand-2');
  });

  it('is null once the queue is emptied', () => {
    expect(selectionAfterReview([candidate(1)], 'cand-1')).toBeNull();
  });

  it('is deterministic for the oldest card', () => {
    expect(selectionAfterReview(queue, 'cand-1')).toBe('cand-2');
  });
});
