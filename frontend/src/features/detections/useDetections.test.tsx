import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useDetections } from './useDetections';
import type { DetectionCandidate, DetectionUpdateData } from '../../types/detections';

function candidate(overrides: Partial<DetectionCandidate> = {}): DetectionCandidate {
  return {
    candidateId: 'cand-1',
    candidateNumber: 1,
    executionId: 'exec-1',
    status: 'PENDING',
    firstObservedAt: '2026-11-04T15:00:00.000Z',
    capturedAt: '2026-11-04T15:00:01.000Z',
    publishedAt: '2026-11-04T15:00:02.000Z',
    reviewedAt: null,
    confidence: 0.8,
    boundingBox: {
      xPx: 1, yPx: 1, widthPx: 10, heightPx: 10, frameWidthPx: 640, frameHeightPx: 480,
    },
    estimatedLocation: null,
    snapshotUrl: '/api/detections/cand-1/snapshot',
    ...overrides,
  };
}

function update(revision: number, c: DetectionCandidate, sessionId = 's1'): DetectionUpdateData {
  return { sessionId, revision, candidate: c };
}

let fetchMock: ReturnType<typeof vi.fn>;

function collection(revision: number, candidates: DetectionCandidate[], sessionId = 's1') {
  return new Response(JSON.stringify({ sessionId, revision, candidates }), { status: 200 });
}

beforeEach(() => {
  fetchMock = vi.fn(async () => collection(0, []));
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

describe('useDetections', () => {
  it('loads the collection once over HTTP, since the socket never sends the list', async () => {
    fetchMock.mockResolvedValueOnce(collection(2, [candidate()]));
    const { result } = renderHook(({ u }) => useDetections(u), {
      initialProps: { u: null as DetectionUpdateData | null },
    });

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(fetchMock).toHaveBeenCalledWith('/api/detections', expect.any(Object));
    expect(result.current.candidates).toHaveLength(1);
  });

  it('adds a new candidate arriving on the socket', async () => {
    const { result, rerender } = renderHook(({ u }) => useDetections(u), {
      initialProps: { u: null as DetectionUpdateData | null },
    });
    await waitFor(() => expect(result.current.loading).toBe(false));

    rerender({ u: update(1, candidate({ candidateId: 'cand-9', candidateNumber: 9 })) });
    await waitFor(() => expect(result.current.candidates).toHaveLength(1));
    expect(result.current.candidates[0].candidateId).toBe('cand-9');
  });

  it('refetches when the revision skips, because an update was missed', async () => {
    fetchMock.mockResolvedValueOnce(collection(1, [candidate()]));
    const { result, rerender } = renderHook(({ u }) => useDetections(u), {
      initialProps: { u: null as DetectionUpdateData | null },
    });
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(fetchMock).toHaveBeenCalledTimes(1);

    // Revision 1 is held locally; 4 means 2 and 3 never arrived.
    rerender({ u: update(4, candidate({ candidateId: 'cand-4' })) });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });

  it('refetches when the backend session changes', async () => {
    fetchMock.mockResolvedValueOnce(collection(5, [candidate()]));
    const { result, rerender } = renderHook(({ u }) => useDetections(u), {
      initialProps: { u: null as DetectionUpdateData | null },
    });
    await waitFor(() => expect(result.current.loading).toBe(false));

    rerender({ u: update(1, candidate(), 'a-different-session') });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });

  it('ignores an update that would move the list backwards', async () => {
    fetchMock.mockResolvedValueOnce(collection(5, [candidate({ status: 'CONFIRMED' })]));
    const { result, rerender } = renderHook(({ u }) => useDetections(u), {
      initialProps: { u: null as DetectionUpdateData | null },
    });
    await waitFor(() => expect(result.current.loading).toBe(false));

    rerender({ u: update(3, candidate({ status: 'PENDING' })) });
    await act(async () => {});
    expect(result.current.candidates[0].status).toBe('CONFIRMED');
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('puts candidates needing review first, newest within each group', async () => {
    fetchMock.mockResolvedValueOnce(
      collection(1, [
        candidate({ candidateId: 'a', candidateNumber: 1, status: 'PENDING' }),
        candidate({ candidateId: 'b', candidateNumber: 2, status: 'CONFIRMED' }),
        candidate({ candidateId: 'c', candidateNumber: 3, status: 'PENDING' }),
      ]),
    );
    const { result } = renderHook(({ u }) => useDetections(u), {
      initialProps: { u: null as DetectionUpdateData | null },
    });
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.candidates.map((c) => c.candidateId)).toEqual(['c', 'a', 'b']);
  });

  it('applies the review response even if no socket update follows', async () => {
    fetchMock.mockResolvedValueOnce(collection(1, [candidate()]));
    const { result } = renderHook(({ u }) => useDetections(u), {
      initialProps: { u: null as DetectionUpdateData | null },
    });
    await waitFor(() => expect(result.current.loading).toBe(false));

    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          sessionId: 's1',
          revision: 2,
          candidate: { ...candidate(), status: 'CONFIRMED', reviewedAt: '2026-11-04T15:05:00.000Z' },
        }),
        { status: 200 },
      ),
    );
    await act(async () => {
      await result.current.review('cand-1', 'CONFIRM');
    });
    expect(result.current.candidates[0].status).toBe('CONFIRMED');
    expect(result.current.reviewing).toBeNull();
  });

  it('reloads after an already-reviewed conflict so the row shows the real decision', async () => {
    fetchMock.mockResolvedValueOnce(collection(1, [candidate()]));
    const { result } = renderHook(({ u }) => useDetections(u), {
      initialProps: { u: null as DetectionUpdateData | null },
    });
    await waitFor(() => expect(result.current.loading).toBe(false));
    const before = fetchMock.mock.calls.length;

    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          detail: { code: 'CANDIDATE_ALREADY_REVIEWED', message: 'already', details: {} },
        }),
        { status: 409 },
      ),
    );
    await act(async () => {
      await result.current.review('cand-1', 'DISMISS');
    });
    expect(result.current.reviewError?.code).toBe('CANDIDATE_ALREADY_REVIEWED');
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(before + 1));
  });
});
