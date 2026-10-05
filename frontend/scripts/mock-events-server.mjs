#!/usr/bin/env node
/**
 * Stand-in for the FastAPI /ws/events endpoint so the frontend can be developed
 * and demoed before the backend slice lands. Sends the frozen telemetry_update
 * contract for a simulated lawnmower search pattern.
 *
 *   npm run mock:events                      # normal feed at 4 Hz
 *   npm run mock:events -- --stall-every 20  # go silent 6s every 20s (tests "Telemetry stopped")
 *   npm run mock:events -- --null-heading    # heading reported as unknown
 *   npm run mock:events -- --port 8000 --hz 10
 */
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

const wss = new WebSocketServer({ port: PORT, path: '/ws/events' });

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

console.log(`mock /ws/events on ws://localhost:${PORT}/ws/events at ${HZ} Hz`);
if (STALL_EVERY_S) console.log(`stalling ${STALL_FOR_S}s every ${STALL_EVERY_S}s`);
if (NULL_HEADING) console.log('heading reported as null');
