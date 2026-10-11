/**
 * Local search-area geometry checks for immediate operator feedback.
 * These are UX checks only. The backend is the authority on whether a search area is usable.
 *
 * Vertices are an ordered list. Segment i runs from vertex i to vertex i + 1; when the polygon
 * is closed, the closing edge runs from the last vertex back to the first. The first vertex is
 * never duplicated at the end of the list.
 */

export interface LatLon {
  latitudeDeg: number;
  longitudeDeg: number;
}

/** Planar point in meters, local to the search area. */
interface XY {
  x: number;
  y: number;
}

const METERS_PER_DEG_LAT = 111_320;

/**
 * Equirectangular projection around a reference latitude. Accurate enough at search-area scale
 * (a few km), and crossing tests are unaffected because the projection is a linear scaling.
 */
function project(p: LatLon, refLatDeg: number): XY {
  return {
    x: p.longitudeDeg * METERS_PER_DEG_LAT * Math.cos((refLatDeg * Math.PI) / 180),
    y: p.latitudeDeg * METERS_PER_DEG_LAT,
  };
}

function orientation(a: XY, b: XY, c: XY): number {
  const v = (b.y - a.y) * (c.x - b.x) - (b.x - a.x) * (c.y - b.y);
  const EPS = 1e-9;
  if (Math.abs(v) < EPS) return 0;
  return v > 0 ? 1 : 2;
}

function onSegment(a: XY, b: XY, p: XY): boolean {
  return (
    Math.min(a.x, b.x) - 1e-9 <= p.x &&
    p.x <= Math.max(a.x, b.x) + 1e-9 &&
    Math.min(a.y, b.y) - 1e-9 <= p.y &&
    p.y <= Math.max(a.y, b.y) + 1e-9
  );
}

/** True if segment ab and segment cd touch or cross at any point, including collinear overlap. */
export function segmentsIntersect(a: LatLon, b: LatLon, c: LatLon, d: LatLon): boolean {
  const ref = a.latitudeDeg;
  const [pa, pb, pc, pd] = [a, b, c, d].map((p) => project(p, ref));
  const o1 = orientation(pa, pb, pc);
  const o2 = orientation(pa, pb, pd);
  const o3 = orientation(pc, pd, pa);
  const o4 = orientation(pc, pd, pb);

  if (o1 !== o2 && o3 !== o4) return true;
  if (o1 === 0 && onSegment(pa, pb, pc)) return true;
  if (o2 === 0 && onSegment(pa, pb, pd)) return true;
  if (o3 === 0 && onSegment(pc, pd, pa)) return true;
  if (o4 === 0 && onSegment(pc, pd, pb)) return true;
  return false;
}

/** Straight-line distance in meters (local projection; fine at search-area scale). */
export function distanceM(a: LatLon, b: LatLon): number {
  const pa = project(a, a.latitudeDeg);
  const pb = project(b, a.latitudeDeg);
  return Math.hypot(pa.x - pb.x, pa.y - pb.y);
}

/** Clicks closer than this to the previous vertex are treated as accidental double-clicks. */
export const MIN_VERTEX_SPACING_M = 0.5;

/**
 * While drawing an open polygon: can `candidate` be committed as the next vertex?
 * The new segment (last vertex -> candidate) may share an endpoint with the immediately
 * previous segment, but must not touch any other existing segment.
 */
export function canAppendVertex(vertices: LatLon[], candidate: LatLon): boolean {
  const n = vertices.length;
  if (n === 0) return true;
  const last = vertices[n - 1];
  if (distanceM(last, candidate) < MIN_VERTEX_SPACING_M) return false;
  if (n < 2) return true;
  // Existing segments 0..n-2; skip segment n-2 because it ends at `last`.
  for (let i = 0; i < n - 2; i++) {
    if (segmentsIntersect(last, candidate, vertices[i], vertices[i + 1])) return false;
  }
  return true;
}

/**
 * Can the open polygon be closed (last vertex -> first vertex)?
 * The closing edge may meet the first edge at the first vertex and the last edge at the last vertex.
 */
export function canClose(vertices: LatLon[]): boolean {
  const n = vertices.length;
  if (n < 3) return false;
  const first = vertices[0];
  const last = vertices[n - 1];
  // Skip segment 0 (starts at first) and segment n-2 (ends at last).
  for (let i = 1; i < n - 2; i++) {
    if (segmentsIntersect(last, first, vertices[i], vertices[i + 1])) return false;
  }
  return true;
}

/** For a closed polygon: no two non-adjacent edges touch. */
export function isSimplePolygon(vertices: LatLon[]): boolean {
  const n = vertices.length;
  if (n < 3) return false;
  const edge = (i: number): [LatLon, LatLon] => [vertices[i], vertices[(i + 1) % n]];
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      const adjacent = j === i + 1 || (i === 0 && j === n - 1);
      if (adjacent) continue;
      const [a, b] = edge(i);
      const [c, d] = edge(j);
      if (segmentsIntersect(a, b, c, d)) return false;
    }
  }
  return true;
}

/** Polygon area in square meters (shoelace on the local projection). */
export function polygonAreaM2(vertices: LatLon[]): number {
  if (vertices.length < 3) return 0;
  const ref = vertices[0].latitudeDeg;
  const pts = vertices.map((v) => project(v, ref));
  let sum = 0;
  for (let i = 0; i < pts.length; i++) {
    const a = pts[i];
    const b = pts[(i + 1) % pts.length];
    sum += a.x * b.y - b.x * a.y;
  }
  return Math.abs(sum) / 2;
}

export type SearchAreaIssue = 'OPEN' | 'TOO_FEW_VERTICES' | 'SELF_INTERSECTION' | 'DEGENERATE_AREA';

/** Below this the polygon is effectively a line; the backend would reject it anyway. */
const MIN_AREA_M2 = 1;

/** null means the polygon is locally usable and Generate Mission Plan can be offered. */
export function searchAreaIssue(vertices: LatLon[], closed: boolean): SearchAreaIssue | null {
  if (vertices.length < 3) return 'TOO_FEW_VERTICES';
  if (!closed) return 'OPEN';
  if (!isSimplePolygon(vertices)) return 'SELF_INTERSECTION';
  if (polygonAreaM2(vertices) < MIN_AREA_M2) return 'DEGENERATE_AREA';
  return null;
}
