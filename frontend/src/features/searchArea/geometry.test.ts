import { describe, expect, it } from 'vitest';
import {
  canAppendVertex,
  canClose,
  isSimplePolygon,
  polygonAreaM2,
  searchAreaIssue,
  segmentsIntersect,
  type LatLon,
} from './geometry';

/** Small helper: points on a ~1 m grid near Austin so tests read like graph paper. */
const BASE = { lat: 30.2291, lon: -97.7555 };
const STEP = 0.00001; // ~1.1 m
const p = (x: number, y: number): LatLon => ({
  latitudeDeg: BASE.lat + y * STEP,
  longitudeDeg: BASE.lon + x * STEP,
});

describe('segmentsIntersect', () => {
  it('detects a proper crossing', () => {
    expect(segmentsIntersect(p(0, 0), p(10, 10), p(0, 10), p(10, 0))).toBe(true);
  });
  it('ignores separated segments', () => {
    expect(segmentsIntersect(p(0, 0), p(10, 0), p(0, 5), p(10, 5))).toBe(false);
  });
  it('counts collinear overlap and touching endpoints', () => {
    expect(segmentsIntersect(p(0, 0), p(10, 0), p(5, 0), p(15, 0))).toBe(true);
    expect(segmentsIntersect(p(0, 0), p(10, 0), p(10, 0), p(10, 10))).toBe(true);
  });
});

describe('drawing an open polygon', () => {
  const zigzag = [p(0, 0), p(20, 0), p(20, 20)];

  it('allows a segment that shares only the previous endpoint', () => {
    expect(canAppendVertex(zigzag, p(0, 20))).toBe(true);
  });

  it('blocks a segment that crosses an earlier non-adjacent segment', () => {
    // From (20,20) to (10,-10) crosses segment (0,0)-(20,0).
    expect(canAppendVertex(zigzag, p(10, -10))).toBe(false);
  });

  it('blocks accidental double-clicks on the same spot', () => {
    expect(canAppendVertex(zigzag, p(20, 20))).toBe(false);
  });

  it('closes when the closing edge only meets the first and last edges', () => {
    expect(canClose([...zigzag, p(0, 20)])).toBe(true);
  });

  it('refuses to close with fewer than three vertices', () => {
    expect(canClose([p(0, 0), p(10, 0)])).toBe(false);
  });

  it('refuses to close when the closing edge would cross the polygon', () => {
    // Every vertex was legal to place, but the edge back to (0,0) would cross edge 1.
    const hook = [p(0, 0), p(10, -10), p(20, 0), p(20, 20)];
    expect(canAppendVertex(hook, p(25, -20))).toBe(true);
    expect(canClose([...hook, p(25, -20)])).toBe(false);
  });
});

describe('closed polygons', () => {
  const square = [p(0, 0), p(20, 0), p(20, 20), p(0, 20)];
  const bowtie = [p(0, 0), p(20, 20), p(20, 0), p(0, 20)];

  it('accepts a simple polygon and rejects a bow-tie', () => {
    expect(isSimplePolygon(square)).toBe(true);
    expect(isSimplePolygon(bowtie)).toBe(false);
  });

  it('computes area in square meters', () => {
    const side = 20 * STEP * 111_320; // meters north-south
    const area = polygonAreaM2(square);
    // East-west sides shrink by cos(latitude); allow 1% tolerance.
    const expected = side * side * Math.cos((BASE.lat * Math.PI) / 180);
    expect(Math.abs(area - expected) / expected).toBeLessThan(0.01);
  });

  it('reports the first blocking issue', () => {
    expect(searchAreaIssue(square.slice(0, 2), false)).toBe('TOO_FEW_VERTICES');
    expect(searchAreaIssue(square, false)).toBe('OPEN');
    expect(searchAreaIssue(bowtie, true)).toBe('SELF_INTERSECTION');
    expect(searchAreaIssue([p(0, 0), p(10, 0), p(20, 0)], true)).toBe('DEGENERATE_AREA');
    expect(searchAreaIssue(square, true)).toBeNull();
  });
});
