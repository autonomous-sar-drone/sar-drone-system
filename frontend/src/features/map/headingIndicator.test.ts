import { describe, expect, it } from 'vitest';
import { HEADING_SEGMENT_M, headingSegment, type DronePose } from './headingIndicator';

const AT: DronePose = { latitudeDeg: 30.2291, longitudeDeg: -97.7555, headingDeg: 0 };

function pose(headingDeg: number | null): DronePose {
  return { ...AT, headingDeg };
}

describe('headingSegment', () => {
  it('returns null when heading is unknown, so no direction is implied', () => {
    expect(headingSegment(pose(null))).toBeNull();
  });

  it('starts at the drone position', () => {
    const segment = headingSegment(pose(45));
    expect(segment).not.toBeNull();
    expect(segment![0]).toEqual({ latitudeDeg: AT.latitudeDeg, longitudeDeg: AT.longitudeDeg });
  });

  it('points due north at 0 degrees', () => {
    const [, end] = headingSegment(pose(0))!;
    expect(end.latitudeDeg).toBeGreaterThan(AT.latitudeDeg);
    expect(end.longitudeDeg).toBeCloseTo(AT.longitudeDeg, 10);
  });

  it('points due east at 90 degrees', () => {
    const [, end] = headingSegment(pose(90))!;
    expect(end.longitudeDeg).toBeGreaterThan(AT.longitudeDeg);
    expect(end.latitudeDeg).toBeCloseTo(AT.latitudeDeg, 10);
  });

  it('points due south at 180 degrees', () => {
    const [, end] = headingSegment(pose(180))!;
    expect(end.latitudeDeg).toBeLessThan(AT.latitudeDeg);
    expect(end.longitudeDeg).toBeCloseTo(AT.longitudeDeg, 10);
  });

  it('points due west at 270 degrees', () => {
    const [, end] = headingSegment(pose(270))!;
    expect(end.longitudeDeg).toBeLessThan(AT.longitudeDeg);
    expect(end.latitudeDeg).toBeCloseTo(AT.latitudeDeg, 10);
  });

  it('normalizes headings outside 0-360 the same way the readout does', () => {
    expect(headingSegment(pose(-90))).toEqual(headingSegment(pose(270)));
    expect(headingSegment(pose(450))).toEqual(headingSegment(pose(90)));
  });

  it('spans the requested length in meters', () => {
    const [, end] = headingSegment(pose(0), 100)!;
    const meters = (end.latitudeDeg - AT.latitudeDeg) * 111_320;
    expect(meters).toBeCloseTo(100, 3);
  });

  it('stretches longitude with latitude so the segment stays the right length', () => {
    const equator = headingSegment({ latitudeDeg: 0, longitudeDeg: 0, headingDeg: 90 }, 100)!;
    const high = headingSegment({ latitudeDeg: 60, longitudeDeg: 0, headingDeg: 90 }, 100)!;
    // cos(60 degrees) = 0.5, so the same distance east spans twice the longitude.
    expect(high[1].longitudeDeg).toBeCloseTo(equator[1].longitudeDeg * 2, 6);
  });

  it('survives the poles instead of dividing by zero', () => {
    const segment = headingSegment({ latitudeDeg: 90, longitudeDeg: 0, headingDeg: 90 }, 100);
    expect(segment).not.toBeNull();
    expect(Number.isFinite(segment![1].longitudeDeg)).toBe(true);
  });

  it('rejects a non-positive or non-finite length', () => {
    expect(headingSegment(pose(90), 0)).toBeNull();
    expect(headingSegment(pose(90), -10)).toBeNull();
    expect(headingSegment(pose(90), Number.NaN)).toBeNull();
  });

  it('defaults to the exported segment length', () => {
    expect(headingSegment(pose(0))).toEqual(headingSegment(pose(0), HEADING_SEGMENT_M));
  });
});
