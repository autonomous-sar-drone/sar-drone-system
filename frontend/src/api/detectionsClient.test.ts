import { describe, expect, it, vi } from 'vitest';
import {
  fetchDetections,
  parseCandidate,
  parseCollection,
  reviewCandidate,
} from './detectionsClient';

const CANDIDATE = {
  candidateId: 'cand-1',
  candidateNumber: 1,
  executionId: 'exec-1',
  status: 'PENDING',
  firstObservedAt: '2026-11-04T15:00:00.000Z',
  capturedAt: '2026-11-04T15:00:01.000Z',
  publishedAt: '2026-11-04T15:00:02.000Z',
  reviewedAt: null,
  confidence: 0.82,
  boundingBox: {
    xPx: 100, yPx: 80, widthPx: 40, heightPx: 90,
    frameWidthPx: 1280, frameHeightPx: 720,
  },
  estimatedLocation: {
    latitudeDeg: 30.2291,
    longitudeDeg: -97.7555,
    method: 'TERRAIN_RAYCAST',
    quality: 'TERRAIN_ADJUSTED',
  },
  snapshotUrl: '/api/detections/cand-1/snapshot',
};

function jsonResponse(body: unknown, status = 200): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

describe('parseCandidate', () => {
  it('reads a full candidate', () => {
    const parsed = parseCandidate(CANDIDATE);
    expect(parsed?.candidateId).toBe('cand-1');
    expect(parsed?.estimatedLocation?.quality).toBe('TERRAIN_ADJUSTED');
    expect(parsed?.boundingBox.frameWidthPx).toBe(1280);
  });

  it('accepts the explicit nulls this contract sends', () => {
    const parsed = parseCandidate({
      ...CANDIDATE, executionId: null, reviewedAt: null, estimatedLocation: null,
    });
    expect(parsed).not.toBeNull();
    expect(parsed?.executionId).toBeNull();
    expect(parsed?.estimatedLocation).toBeNull();
  });

  it('rejects a confidence outside 0 to 1', () => {
    expect(parseCandidate({ ...CANDIDATE, confidence: 1.4 })).toBeNull();
    expect(parseCandidate({ ...CANDIDATE, confidence: -0.1 })).toBeNull();
  });

  it('rejects a zero-sized frame that would divide by zero downstream', () => {
    const box = { ...CANDIDATE.boundingBox, frameWidthPx: 0 };
    expect(parseCandidate({ ...CANDIDATE, boundingBox: box })).toBeNull();
  });

  it('rejects a status or location method it does not know', () => {
    expect(parseCandidate({ ...CANDIDATE, status: 'MAYBE' })).toBeNull();
    const loc = { ...CANDIDATE.estimatedLocation, method: 'GUESSWORK' };
    // An unusable location drops to null rather than failing the whole candidate.
    expect(parseCandidate({ ...CANDIDATE, estimatedLocation: loc })?.estimatedLocation).toBeNull();
  });

  it('rejects an out-of-range coordinate', () => {
    const loc = { ...CANDIDATE.estimatedLocation, latitudeDeg: 130 };
    expect(parseCandidate({ ...CANDIDATE, estimatedLocation: loc })?.estimatedLocation).toBeNull();
  });
});

describe('parseCollection', () => {
  it('keeps the good rows when one candidate is malformed', () => {
    const collection = parseCollection({
      sessionId: 's1',
      revision: 4,
      candidates: [CANDIDATE, { candidateId: 'broken' }, { ...CANDIDATE, candidateId: 'cand-2' }],
    });
    // Losing one row beats blanking the panel during a search.
    expect(collection?.candidates.map((c) => c.candidateId)).toEqual(['cand-1', 'cand-2']);
  });

  it('rejects a body that is not a collection', () => {
    expect(parseCollection({ sessionId: 's1', revision: 1 })).toBeNull();
    expect(parseCollection(null)).toBeNull();
  });
});

describe('detection requests', () => {
  it('loads the collection', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      jsonResponse({ sessionId: 's1', revision: 2, candidates: [CANDIDATE] }),
    );
    const result = await fetchDetections({ fetchImpl });
    expect(fetchImpl).toHaveBeenCalledWith('/api/detections', expect.any(Object));
    expect(result.ok).toBe(true);
    if (result.ok) expect(result.value.candidates).toHaveLength(1);
  });

  it('posts the decision and escapes the id', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      jsonResponse({ sessionId: 's1', revision: 3, candidate: { ...CANDIDATE, status: 'CONFIRMED' } }),
    );
    const result = await reviewCandidate('a/b', 'CONFIRM', { fetchImpl });
    expect(fetchImpl.mock.calls[0][0]).toBe('/api/detections/a%2Fb/review');
    expect(JSON.parse(fetchImpl.mock.calls[0][1].body)).toEqual({ decision: 'CONFIRM' });
    expect(result.ok).toBe(true);
    if (result.ok) expect(result.value.candidate.status).toBe('CONFIRMED');
  });

  it('surfaces an already-reviewed conflict as a structured error', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      jsonResponse({
        detail: {
          code: 'CANDIDATE_ALREADY_REVIEWED',
          message: 'This candidate has already been reviewed.',
          details: { candidateId: 'cand-1', status: 'CONFIRMED' },
        },
      }, 409),
    );
    const result = await reviewCandidate('cand-1', 'DISMISS', { fetchImpl });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error.code).toBe('CANDIDATE_ALREADY_REVIEWED');
  });

  it('reports an unreachable backend rather than throwing', async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new TypeError('failed to fetch'));
    const result = await fetchDetections({ fetchImpl });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error.code).toBe('NETWORK_ERROR');
  });

  it('rethrows an abort so the caller can ignore it', async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new DOMException('aborted', 'AbortError'));
    await expect(fetchDetections({ fetchImpl })).rejects.toThrow('aborted');
  });
});
