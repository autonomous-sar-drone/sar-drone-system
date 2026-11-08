import { describe, expect, it } from 'vitest';
import {
  createMarkerElement,
  createPopupElement,
  markerModels,
  updateMarkerElement,
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

describe('updateMarkerElement', () => {
  it('replaces the triangle SVG with the confirmed person icon in place', () => {
    const [pending] = markerModels([candidate(1, 'PENDING')]);
    const [confirmed] = markerModels([candidate(1, 'CONFIRMED')]);
    const el = createMarkerElement(pending);
    expect(el.querySelector('.detect-marker__shape circle')).toBeNull();
    updateMarkerElement(el, confirmed);
    expect(el.className).toContain('detect-marker--confirmed');
    expect(el.querySelector('.detect-marker__shape circle')).not.toBeNull();
    expect(el.getAttribute('aria-label')).toBe('Confirmed person 1');
    expect(el.querySelector('.detect-marker__number')?.textContent).toBe('1');
  });
});

describe('createPopupElement', () => {
  it('previews the candidate without offering a decision', () => {
    const el = createPopupElement(candidate(3), NOW);
    expect(el.querySelector('img')?.getAttribute('src')).toBe('/api/detections/cand-3/snapshot');
    expect(el.textContent).toContain('Possible person #3');
    expect(el.textContent).toContain('82% confidence');
    expect(el.querySelector('time')?.getAttribute('datetime')).toBe('2026-11-07T15:00:00.000Z');
    expect(el.querySelector('time')?.getAttribute('title')).toContain('2m ago');
    expect(el.textContent).toContain('30.22910° N, 97.75550° W');
    expect(el.textContent).toContain('Estimated (terrain-adjusted)');
    // Reviewing from a hover popup is too easy to do by accident.
    expect(el.querySelector('button')).toBeNull();
  });

  it('labels the point as historical, not the drone now', () => {
    const el = createPopupElement(candidate(1), NOW);
    expect(el.textContent).toMatch(/Last seen here/);
    expect(el.textContent).not.toMatch(/not the drone’s current position/i);
  });


  it('keeps the snapshot nearly full width with details in a separate region', () => {
    const el = createPopupElement(candidate(1, 'CONFIRMED'), NOW);
    expect(el.children[0]?.className).toBe('detect-popup__thumb');
    expect(el.children[1]?.className).toBe('detect-popup__details');
    expect(el.textContent).toContain('Confirmed');
  });

  it('formats southern and eastern coordinate hemispheres correctly', () => {
    const other = candidate(8);
    other.estimatedLocation = {
      latitudeDeg: -33.8651, longitudeDeg: 151.2099,
      method: 'TERRAIN_RAYCAST', quality: 'TERRAIN_ADJUSTED',
    };
    const el = createPopupElement(other, NOW);
    expect(el.textContent).toContain('33.86510° S, 151.20990° E');
  });
  it('builds content as text, so candidate data can never become markup', () => {
    const hostile = { ...candidate(1), candidateNumber: 5 };
    const el = createPopupElement(hostile, NOW);
    const title = el.querySelector('.detect-popup__title');
    expect(title?.textContent).toBe('Possible person #5');
    expect(title?.children).toHaveLength(0);
  });

  it('says so when there is no ground position', () => {
    const el = createPopupElement(candidate(1, 'PENDING', null), NOW);
    expect(el.textContent).toContain('No ground position could be worked out');
  });
});
