import type { LatLon } from '../features/searchArea/geometry';
import type {
  PlanError,
  PlanningSettings,
  ProposedPlan,
  RoutePreviewRequest,
  RouteWaypoint,
} from '../types/routes';

export type PreviewResult = { ok: true; plan: ProposedPlan } | { ok: false; error: PlanError };

export function buildPreviewRequest(
  vertices: LatLon[],
  planning?: PlanningSettings,
): RoutePreviewRequest {
  // Ordered vertices only: no duplicated closing vertex and no `closed` flag (locked contract).
  const body: RoutePreviewRequest = {
    searchArea: {
      vertices: vertices.map(({ latitudeDeg, longitudeDeg }) => ({ latitudeDeg, longitudeDeg })),
    },
  };
  if (planning) body.planning = planning;
  return body;
}

const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null;

function parseWaypoint(raw: unknown): RouteWaypoint | null {
  if (!isObj(raw)) return null;
  if (!isNum(raw.latitudeDeg) || !isNum(raw.longitudeDeg) || !isNum(raw.altitudeM)) return null;
  if (raw.altitudeReference !== 'HOME_RELATIVE') return null;
  return {
    latitudeDeg: raw.latitudeDeg,
    longitudeDeg: raw.longitudeDeg,
    altitudeM: raw.altitudeM,
    altitudeReference: raw.altitudeReference,
  };
}

export function parsePlan(raw: unknown): ProposedPlan | null {
  if (!isObj(raw) || typeof raw.planId !== 'string') return null;
  const route = raw.route;
  const metrics = raw.metrics;
  const used = raw.planningUsed;
  if (!isObj(route) || !Array.isArray(route.waypoints) || route.waypoints.length === 0) return null;
  if (!isObj(metrics) || !isNum(metrics.distanceToStartM) || !isNum(metrics.routeDistanceM)) return null;
  if (!isObj(used) || !isNum(used.searchAltitudeM) || !isNum(used.laneSpacingM)) return null;

  const waypoints: RouteWaypoint[] = [];
  for (const w of route.waypoints) {
    const parsed = parseWaypoint(w);
    if (!parsed) return null;
    waypoints.push(parsed);
  }
  return {
    planId: raw.planId,
    route: { waypoints },
    metrics: { distanceToStartM: metrics.distanceToStartM, routeDistanceM: metrics.routeDistanceM },
    planningUsed: { searchAltitudeM: used.searchAltitudeM, laneSpacingM: used.laneSpacingM },
  };
}

/** Reads the shared `{ detail: { code, message, details } }` error shape. */
export function parseError(status: number, raw: unknown): PlanError {
  const detail = isObj(raw) ? raw.detail : undefined;
  if (isObj(detail) && typeof detail.code === 'string') {
    return {
      code: detail.code,
      message: typeof detail.message === 'string' ? detail.message : '',
      details: isObj(detail.details) ? detail.details : {},
    };
  }
  // FastAPI request-validation errors arrive as { detail: [...] } with status 422.
  return {
    code: 'UNEXPECTED_RESPONSE',
    message: `The backend returned an unexpected response (HTTP ${status}).`,
    details: { status, body: raw },
  };
}

export async function previewRoute(
  vertices: LatLon[],
  options: { planning?: PlanningSettings; signal?: AbortSignal; fetchImpl?: typeof fetch } = {},
): Promise<PreviewResult> {
  const fetchImpl = options.fetchImpl ?? fetch;
  let response: Response;
  try {
    response = await fetchImpl('/api/routes/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(buildPreviewRequest(vertices, options.planning)),
      signal: options.signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') throw err;
    return {
      ok: false,
      error: { code: 'NETWORK_ERROR', message: 'Could not reach the backend.', details: {} },
    };
  }

  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    /* non-JSON body; handled below */
  }

  if (response.ok) {
    const plan = parsePlan(body);
    if (plan) return { ok: true, plan };
    return {
      ok: false,
      error: {
        code: 'UNEXPECTED_RESPONSE',
        message: 'The backend returned a route in an unexpected format.',
        details: { body },
      },
    };
  }
  return { ok: false, error: parseError(response.status, body) };
}
