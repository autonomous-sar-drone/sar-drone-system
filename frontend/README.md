# Operator console (frontend)

React + TypeScript + Vite operator interface for the Autonomous SAR Drone System.
Current scope is the Week 5 slice: live telemetry from `/ws/events`.

## Run it

```bash
cd frontend
npm install
npm run dev            # http://localhost:5173
```

The dev server proxies `/ws` and `/api` to the backend at `http://localhost:8000`.
Point it elsewhere with `SAR_BACKEND_URL=http://host:port npm run dev`.

### Without the backend

`scripts/mock-events-server.mjs` speaks the same `/ws/events` contract, flying a
lawnmower pattern at 2 m/s and 25 m. Run it in a second terminal instead of FastAPI:

```bash
npm run mock:events                          # normal 4 Hz feed
npm run mock:events -- --stall-every 20      # goes silent 6 s every 20 s
npm run mock:events -- --null-heading        # heading reported as unknown
```

Stop and restart the mock to see the offline state and automatic reconnect.

## Test

```bash
npm test               # vitest: parser, link status, client reconnect, panel rendering
npm run build          # type-check + production build
npm run lint
```

## Configuration

Copy `.env.example` to `.env.local` (git-ignored). Supported values:

| Variable | Default | Meaning |
| --- | --- | --- |
| `VITE_EVENTS_URL` | same origin `/ws/events` | Full WebSocket URL if not using the dev proxy |
| `VITE_TELEMETRY_STALE_MS` | `3000` | Silence longer than this marks telemetry stale |

## Layout

```
src/
  types/telemetry.ts          frozen /ws/events contract (mirror of backend schema)
  api/parseEvent.ts           runtime validation; unknown message types are ignored, not errors
  api/eventsClient.ts         WebSocket connection with capped exponential-backoff reconnect
  core/config.ts              VITE_* configuration
  features/telemetry/         hook, link-status logic, formatting, panel UI
scripts/mock-events-server.mjs
```

## Link states

| State | Meaning |
| --- | --- |
| Connecting | No connection yet |
| Connected, no telemetry yet | Backend reachable, SITL not publishing |
| Live telemetry | Samples arriving within the stale threshold |
| Telemetry stopped | Socket open, samples stopped; last known values stay on screen |
| Backend unreachable | Socket closed, reconnecting; last known values stay on screen |

"Feed delay" is browser receive time minus the backend `timestamp`, so it measures
backend-to-browser delay. It is not full drone-to-dashboard delay.
