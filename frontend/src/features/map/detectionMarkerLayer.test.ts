import { describe, expect, it } from 'vitest';
import {
  createMarkerElement,
  createPopupElement,
  markerModels,
} from './detectionMarkerLayer';
import type {
  CandidateStatus,
  DetectionCandidate,
  LocationQuality,
} from '../../types/detections';

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

describe('markerModels', () => {
  it('marks pending and confirmed candidates apart', () => {
    const models = markerModels([candidate(1), candidate(2, 'CONFIRMED')]);
    expect(models.map((m) => m.confirmed)).toEqual([false, true]);
  });

  it('uses longitude-first order for MapLibre', () => {
    const [model] = markerModels([candidate(1)]);
    expect(model.lngLat).toEqual([-97.7555, 30.2291]);
  });

  it('keeps the candidate number as the stable visible identity', () => {
    const [model] = markerModels([candidate(7)]);
    expect(model.candidateNumber).toBe(7);
    expect(model.candidateId).toBe('cand-7');
  });

  it('leaves dismissed, position-less and fallback candidates unmapped', () => {
    const models = markerModels([
      candidate(1),
      candidate(2, 'DISMISSED'),
      candidate(3, 'PENDING', null),
      candidate(4, 'PENDING', 'CAMERA_POSITION_ONLY'),
    ]);
    expect(models.map((m) => m.candidateId)).toEqual(['cand-1']);
  });

  it('does not move a candidate when it is reviewed', () => {
    const [before] = markerModels([candidate(1, 'PENDING')]);
    const [after] = markerModels([candidate(1, 'CONFIRMED')]);
    expect(after.lngLat).toEqual(before.lngLat);
    expect(after.candidateNumber).toBe(before.candidateNumber);
  });
});

describe('createMarkerElement', () => {
  it('distinguishes the states by shape, not only colour', () => {
    const pending = createMarkerElement(markerModels([candidate(1)])[0]);
    const confirmed = createMarkerElement(markerModels([candidate(2, 'CONFIRMED')])[0]);
    expect(pending.className).toContain('detect-marker--pending');
    expect(confirmed.className).toContain('detect-marker--confirmed');
    // A triangle path versus a head-and-shoulders mark.
    expect(pending.querySelector('path')?.getAttribute('d')).toMatch(/^M12 3/);
    expect(confirmed.querySelector('circle')).not.toBeNull();
  });

  it('shows the candidate number and names the state for a screen reader', () => {
    const el = createMarkerElement(markerModels([candidate(4, 'CONFIRMED')])[0]);
    expect(el.querySelector('.detect-marker__number')?.textContent).toBe('4');
    expect(el.getAttribute('aria-label')).toBe('Confirmed person 4');
  });
});

describe('createPopupElement', () => {
  it('previews the candidate without offering a decision', () => {
    const el = createPopupElement(candidate(3), NOW);
    expect(el.querySelector('img')?.getAttribute('src')).toBe('/api/detections/cand-3/snapshot');
    expect(el.textContent).toContain('Possible person 3');
    expect(el.textContent).toContain('82% confidence');
    expect(el.textContent).toContain('2m ago');
    expect(el.textContent).toContain('Adjusted for terrain');
    // Reviewing from a hover popup is too easy to do by accident.
    expect(el.querySelector('button')).toBeNull();
  });

  it('labels the point as historical, not the drone now', () => {
    const el = createPopupElement(candidate(1), NOW);
    expect(el.textContent).toMatch(/Last seen here/);
  });

  it('builds content as text, so candidate data can never become markup', () => {
    const hostile = { ...candidate(1), candidateNumber: 5 };
    const el = createPopupElement(hostile, NOW);
    const title = el.querySelector('.detect-popup__title');
    expect(title?.textContent).toBe('Possible person 5');
    expect(title?.children).toHaveLength(0);
  });

  it('says so when there is no ground position', () => {
    const el = createPopupElement(candidate(1, 'PENDING', null), NOW);
    expect(el.textContent).toContain('No ground position could be worked out');
  });
});
