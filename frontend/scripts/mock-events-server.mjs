#!/usr/bin/env node
/**
 * Stand-in for the FastAPI backend so the frontend can be developed and demoed alone.
 *   - /ws/events: frozen telemetry_update contract for a simulated lawnmower flight
 *   - POST /api/routes/preview: W06 route preview contract, including its error shapes
 *
 *   npm run mock:events                      # normal feed at 4 Hz
 *   npm run mock:events -- --stall-every 20  # go silent 6s every 20s (tests "Telemetry stopped")
 *   npm run mock:events -- --null-heading    # heading reported as unknown
 *   npm run mock:events -- --port 8000 --hz 10
 *   npm run mock:events -- --reject ROUTE_TOO_LONG   # every preview fails with that code
 */
import { createServer } from 'node:http';
import { WebSocketServer } from 'ws';

const args = process.argv.slice(2);
const flag = (name) => args.includes(`--${name}`);
const opt = (name, fallback) => {
  const i = args.indexOf(`--${name}`);
  return i >= 0 && args[i + 1] !== undefined ? Number(args[i + 1]) : fallback;
};

const PORT = opt('port', 8000);
const HZ = opt('hz', 4);
const STALL_EVERY_S = opt('stall-every', 0);
const STALL_FOR_S = opt('stall-for', 6);
const NULL_HEADING = flag('null-heading');
const REJECT_IDX = args.indexOf('--reject');
const FORCED_ERROR = REJECT_IDX >= 0 ? args[REJECT_IDX + 1] : null;

// Lawnmower over a ~200 m x 150 m box. Origin is arbitrary; replace with the Webots GPS origin.
const ORIGIN = { lat: 30.2291, lon: -97.7555 };
const M_PER_DEG_LAT = 111_320;
const M_PER_DEG_LON = 111_320 * Math.cos((ORIGIN.lat * Math.PI) / 180);
const LANE_SPACING_M = 30;
const LANE_LENGTH_M = 200;
const LANES = 6;
const SPEED_MPS = 2; // matches WP_SPD 2 in the known-good SITL config
const ALT_M = 25;

const waypoints = [];
for (let i = 0; i < LANES; i++) {
  const y = i * LANE_SPACING_M;
  const [x0, x1] = i % 2 === 0 ? [0, LANE_LENGTH_M] : [LANE_LENGTH_M, 0];
  waypoints.push({ x: x0, y }, { x: x1, y });
}

let pos = { ...waypoints[0] };
let target = 1;
let headingDeg = 90;
let latest = null;
let tick = 0;

function step(dt) {
  const wp = waypoints[target];
  const dx = wp.x - pos.x;
  const dy = wp.y - pos.y;
  const dist = Math.hypot(dx, dy);
  const move = SPEED_MPS * dt;
  if (dist <= move) {
    pos = { ...wp };
    target = (target + 1) % waypoints.length;
  } else {
    pos.x += (dx / dist) * move;
    pos.y += (dy / dist) * move;
    // x = east, y = north; compass heading measured clockwise from north
    headingDeg = ((Math.atan2(dx, dy) * 180) / Math.PI + 360) % 360;
  }
  latest = {
    type: 'telemetry_update',
    data: {
      timestamp: new Date().toISOString(),
      latitudeDeg: ORIGIN.lat + pos.y / M_PER_DEG_LAT,
      longitudeDeg: ORIGIN.lon + pos.x / M_PER_DEG_LON,
      relativeAltitudeM: ALT_M + Math.sin(tick / 10) * 0.3,
      headingDeg: NULL_HEADING ? null : Math.round(headingDeg * 100) / 100,
      groundSpeedMps: SPEED_MPS + Math.sin(tick / 7) * 0.1,
    },
  };
  tick++;
}

function stalled() {
  if (!STALL_EVERY_S) return false;
  const t = (tick / HZ) % STALL_EVERY_S;
  return t > STALL_EVERY_S - STALL_FOR_S;
}

// ---------------------------------------------------------------------------
// POST /api/routes/preview (mock of the backend Geospatial planner)
// ---------------------------------------------------------------------------
const DEFAULT_PLANNING = { searchAltitudeM: 25, laneSpacingM: 20 };
const MAX_TRANSIT_M = 2000;
const MAX_ROUTE_M = 20000;
const MAX_AREA_M2 = 2_000_000;
let planCounter = 0;

function toLocal(v, ref) {
  return {
    x: (v.longitudeDeg - ref.lon) * 111_320 * Math.cos((ref.lat * Math.PI) / 180),
    y: (v.latitudeDeg - ref.lat) * 111_320,
  };
}
function toLatLon(p, ref) {
  return {
    latitudeDeg: ref.lat + p.y / 111_320,
    longitudeDeg: ref.lon + p.x / (111_320 * Math.cos((ref.lat * Math.PI) / 180)),
  };
}
function cross(o, a, b) {
  return (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
}
function segsCross(a, b, c, d) {
  const d1 = cross(c, d, a), d2 = cross(c, d, b), d3 = cross(a, b, c), d4 = cross(a, b, d);
  return d1 * d2 < 0 && d3 * d4 < 0;
}
function selfIntersects(pts) {
  const n = pts.length;
  for (let i = 0; i < n; i++)
    for (let j = i + 2; j < n; j++) {
      if (i === 0 && j === n - 1) continue;
      if (segsCross(pts[i], pts[(i + 1) % n], pts[j], pts[(j + 1) % n])) return true;
    }
  return false;
}
function area(pts) {
  let s = 0;
  for (let i = 0; i < pts.length; i++) s += cross({ x: 0, y: 0 }, pts[i], pts[(i + 1) % pts.length]);
  return Math.abs(s) / 2;
}
const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);

function planRoute(body) {
  const vertices = body?.searchArea?.vertices;
  const planning = { ...DEFAULT_PLANNING, ...(body?.planning ?? {}) };
  const fail = (status, code, message, details = {}) => ({ status, body: { detail: { code, message, details } } });

  if (FORCED_ERROR) {
    const details = FORCED_ERROR === 'SEARCH_AREA_OUT_OF_RANGE'
      ? { distanceToStartM: 4820.0, maxTransitDistanceM: MAX_TRANSIT_M }
      : FORCED_ERROR === 'INVALID_SEARCH_AREA' ? { reason: 'AREA_TOO_LARGE' } : {};
    return fail(422, FORCED_ERROR, `Forced by --reject ${FORCED_ERROR}`, details);
  }
  if (!Array.isArray(vertices) || vertices.length < 3)
    return fail(422, 'INVALID_SEARCH_AREA', 'A search area needs at least three vertices.', { reason: 'TOO_FEW_VERTICES' });
  if (!latest)
    return fail(409, 'VEHICLE_POSITION_UNAVAILABLE', 'No current vehicle position.');

  const ref = { lat: vertices[0].latitudeDeg, lon: vertices[0].longitudeDeg };
  const pts = vertices.map((v) => toLocal(v, ref));
  if (selfIntersects(pts))
    return fail(422, 'INVALID_SEARCH_AREA', 'Search area edges cross.', { reason: 'SELF_INTERSECTION' });
  const a = area(pts);
  if (a < 1) return fail(422, 'INVALID_SEARCH_AREA', 'Search area has no area.', { reason: 'DEGENERATE_AREA' });
  if (a > MAX_AREA_M2) return fail(422, 'INVALID_SEARCH_AREA', 'Search area too large.', { reason: 'AREA_TOO_LARGE' });

  // Back-and-forth east-west passes, clipped to the polygon with a scanline.
  const ys = pts.map((p) => p.y);
  const minY = Math.min(...ys), maxY = Math.max(...ys);
  const lanes = [];
  for (let y = minY + planning.laneSpacingM / 2; y < maxY; y += planning.laneSpacingM) {
    const xs = [];
    for (let i = 0; i < pts.length; i++) {
      const p1 = pts[i], p2 = pts[(i + 1) % pts.length];
      if ((p1.y <= y && p2.y > y) || (p2.y <= y && p1.y > y)) {
        xs.push(p1.x + ((y - p1.y) / (p2.y - p1.y)) * (p2.x - p1.x));
      }
    }
    if (xs.length >= 2) lanes.push({ y, x0: Math.min(...xs), x1: Math.max(...xs) });
  }
  if (lanes.length === 0) {
    // Area thinner than one lane: fly through the centroid instead.
    const cx = pts.reduce((s, p) => s + p.x, 0) / pts.length;
    const cy = pts.reduce((s, p) => s + p.y, 0) / pts.length;
    lanes.push({ y: cy, x0: cx, x1: cx });
  }
  const path = [];
  lanes.forEach((lane, i) => {
    const [a1, b1] = i % 2 === 0 ? [lane.x0, lane.x1] : [lane.x1, lane.x0];
    path.push({ x: a1, y: lane.y }, { x: b1, y: lane.y });
  });

  const drone = toLocal(latest.data, ref);
  const distanceToStartM = dist(drone, path[0]);
  let routeDistanceM = 0;
  for (let i = 1; i < path.length; i++) routeDistanceM += dist(path[i - 1], path[i]);

  if (distanceToStartM > MAX_TRANSIT_M)
    return fail(422, 'SEARCH_AREA_OUT_OF_RANGE', 'The search area is too far from the current vehicle position.', {
      distanceToStartM: Math.round(distanceToStartM * 10) / 10,
      maxTransitDistanceM: MAX_TRANSIT_M,
    });
  if (routeDistanceM > MAX_ROUTE_M)
    return fail(422, 'ROUTE_TOO_LONG', 'The generated route exceeds the route-length policy.', {
      routeDistanceM: Math.round(routeDistanceM), maxRouteDistanceM: MAX_ROUTE_M,
    });

  planCounter += 1;
  return {
    status: 200,
    body: {
      planId: `mock-plan-${planCounter}`,
      route: {
        waypoints: path.map((p) => ({
          ...toLatLon(p, ref),
          altitudeM: planning.searchAltitudeM,
          altitudeReference: 'HOME_RELATIVE',
        })),
      },
      metrics: {
        distanceToStartM: Math.round(distanceToStartM * 10) / 10,
        routeDistanceM: Math.round(routeDistanceM * 10) / 10,
      },
      planningUsed: planning,
    },
  };
}

function sendJson(res, status, body) {
  res.writeHead(status, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(body));
}

const server = createServer((req, res) => {
  if (req.method === 'POST' && req.url === '/api/routes/preview') {
    let raw = '';
    req.on('data', (chunk) => (raw += chunk));
    req.on('end', () => {
      let body;
      try {
        body = JSON.parse(raw);
      } catch {
        return sendJson(res, 422, { detail: [{ msg: 'body is not valid JSON' }] });
      }
      const result = planRoute(body);
      console.log(`POST /api/routes/preview -> ${result.status}${result.status === 200 ? '' : ` ${result.body.detail.code}`}`);
      // Short delay so the UI's "generating" state is visible.
      setTimeout(() => sendJson(res, result.status, result.body), 500);
    });
    return;
  }
  sendJson(res, 404, { detail: 'Not found' });
});

const wss = new WebSocketServer({ server, path: '/ws/events' });

wss.on('connection', (socket) => {
  console.log(`client connected (${wss.clients.size} total)`);
  // Contract rule: a newly connected browser gets the current state immediately.
  if (latest) socket.send(JSON.stringify(latest));
  socket.on('close', () => console.log(`client disconnected (${wss.clients.size} total)`));
});

setInterval(() => {
  step(1 / HZ);
  if (stalled()) return;
  const frame = JSON.stringify(latest);
  for (const client of wss.clients) if (client.readyState === 1) client.send(frame);
}, 1000 / HZ);

server.listen(PORT);
console.log(`mock /ws/events on ws://localhost:${PORT}/ws/events at ${HZ} Hz`);
console.log(`mock POST http://localhost:${PORT}/api/routes/preview`);
if (FORCED_ERROR) console.log(`every preview will fail with ${FORCED_ERROR}`);
if (STALL_EVERY_S) console.log(`stalling ${STALL_FOR_S}s every ${STALL_EVERY_S}s`);
if (NULL_HEADING) console.log('heading reported as null');
