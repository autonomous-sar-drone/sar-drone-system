# Operator console (frontend)

React + TypeScript + Vite operator interface for the Autonomous SAR Drone System.
Current scope: live telemetry from `/ws/events` (W05) and the search-area drawing plus
route-preview workflow against `POST /api/routes/preview` (W06).

## Run it

```bash
cd frontend
npm install
npm run dev            # http://localhost:5173
```

The dev server proxies `/ws` and `/api` to the backend at `http://localhost:8000`.
Point it elsewhere with `SAR_BACKEND_URL=http://host:port npm run dev`.

### Without the backend

`scripts/mock-events-server.mjs` speaks the same `/ws/events` and `/api/routes/preview`
contracts. It flies a lawnmower pattern at 2 m/s and 25 m, and generates real back-and-forth
routes (or the documented errors) for drawn search areas. Run it instead of FastAPI:

```bash
npm run mock:events                          # normal 4 Hz feed
npm run mock:events -- --stall-every 20      # goes silent 6 s every 20 s
npm run mock:events -- --null-heading        # heading reported as unknown
npm run mock:events -- --reject ROUTE_TOO_LONG   # every route preview fails with that code
```

Drawing a search area far from the drone (over 2 km) returns `SEARCH_AREA_OUT_OF_RANGE`
from the mock, the same as the real policy.

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
| `VITE_MAP_STYLE_URL` | development OSM tiles | MapLibre style URL; set to a locally served style for offline use |
| `VITE_MAP_CENTER` | `30.2291,-97.7555` | Where the map opens before telemetry arrives |

The default basemap uses public OpenStreetMap tiles and is **development only**. The
production console must work offline, so the style source is isolated in
`src/features/map/mapConfig.ts` and replaceable through `VITE_MAP_STYLE_URL`.

## Layout

```
src/
  types/telemetry.ts          frozen /ws/events contract (mirror of backend schema)
  api/parseEvent.ts           runtime validation; unknown message types are ignored, not errors
  api/eventsClient.ts         WebSocket connection with capped exponential-backoff reconnect
  core/config.ts              VITE_* configuration
  features/telemetry/         hook, link-status logic, formatting, panel UI
  features/searchArea/        polygon geometry checks (UX only; backend is authoritative)
  features/planning/          mission phase + search area + plan state (reducer), error copy
  features/map/               MapLibre view: search area, route, drone as separate layers
  api/routesClient.ts         POST /api/routes/preview client and contract parsing
  types/routes.ts             route preview contract mirror
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
