import { describe, expect, it } from 'vitest';
import { isMappable } from './detectionMarkers';
import type {
  CandidateStatus,
  DetectionCandidate,
  LocationMethod,
  LocationQuality,
} from '../../types/detections';

function candidate(
  overrides: Partial<DetectionCandidate> = {},
  location: { quality: LocationQuality; method: LocationMethod } | null = {
    quality: 'TERRAIN_ADJUSTED',
    method: 'TERRAIN_RAYCAST',
  },
): DetectionCandidate {
  return {
    candidateId: 'cand-1',
    candidateNumber: 1,
    executionId: 'exec-1',
    status: 'PENDING' as CandidateStatus,
    firstObservedAt: '2026-11-04T15:00:00.000Z',
    capturedAt: '2026-11-04T15:00:00.000Z',
    publishedAt: '2026-11-04T15:00:00.000Z',
    reviewedAt: null,
    confidence: 0.8,
    boundingBox: {
      xPx: 1, yPx: 1, widthPx: 10, heightPx: 10, frameWidthPx: 640, frameHeightPx: 480,
    },
    estimatedLocation: location
      ? {
          latitudeDeg: 30.2291,
          longitudeDeg: -97.7555,
          method: location.method,
          quality: location.quality,
        }
      : null,
    snapshotUrl: '/api/detections/cand-1/snapshot',
    ...overrides,
  };
}

describe('isMappable', () => {
  it('maps a pending or confirmed candidate with a real position', () => {
    expect(isMappable(candidate({ status: 'PENDING' }))).toBe(true);
    expect(isMappable(candidate({ status: 'CONFIRMED' }))).toBe(true);
  });

  it('leaves a dismissed candidate off the map', () => {
    // The operator already decided it is not a person.
    expect(isMappable(candidate({ status: 'DISMISSED' }))).toBe(false);
  });

  it('leaves a candidate with no position off the map', () => {
    expect(isMappable(candidate({}, null))).toBe(false);
  });

  it('never plots a camera-position fallback as a person', () => {
    // Those coordinates are where the DRONE was, not where anyone was seen.
    // A pin here could send a rescuer to the wrong place.
    const fallback = candidate({}, {
      quality: 'CAMERA_POSITION_ONLY',
      method: 'CAMERA_POSITION_FALLBACK',
    });
    expect(isMappable(fallback)).toBe(false);
  });
});
