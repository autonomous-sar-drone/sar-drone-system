import { describe, expect, it, vi } from 'vitest';
import { operatorMessage } from '../features/planning/planErrorCopy';
import { buildPreviewRequest, previewRoute } from './routesClient';

const vertices = [
  { latitudeDeg: 30.2672, longitudeDeg: -97.7431 },
  { latitudeDeg: 30.268, longitudeDeg: -97.7431 },
  { latitudeDeg: 30.268, longitudeDeg: -97.742 },
];

/** Exact success example from the handoff doc, section 4. */
const handoffSuccess = {
  planId: 'plan-abc',
  route: {
    waypoints: [
      { latitudeDeg: 30.2674, longitudeDeg: -97.743, altitudeM: 25.0, altitudeReference: 'HOME_RELATIVE' },
    ],
  },
  metrics: { distanceToStartM: 42.3, routeDistanceM: 615.7 },
  planningUsed: { searchAltitudeM: 25.0, laneSpacingM: 20.0 },
};

/** Exact error example from the handoff doc, section 5. */
const handoffError = {
  detail: {
    code: 'SEARCH_AREA_OUT_OF_RANGE',
    message: 'The search area is too far from the current vehicle position.',
    details: { distanceToStartM: 4820.0, maxTransitDistanceM: 2000.0 },
  },
};

function fakeFetch(status: number, body: unknown) {
  return vi.fn(async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch;
}

describe('buildPreviewRequest', () => {
  it('serializes ordered vertices without a closing duplicate or closed flag', () => {
    const body = buildPreviewRequest([...vertices.map((v) => ({ ...v, extra: 1 }))]);
    expect(body).toEqual({ searchArea: { vertices } });
    expect(JSON.stringify(body)).not.toContain('closed');
  });

  it('omits planning unless provided so backend defaults apply', () => {
    expect('planning' in buildPreviewRequest(vertices)).toBe(false);
    expect(buildPreviewRequest(vertices, { searchAltitudeM: 25, laneSpacingM: 20 }).planning).toEqual({
      searchAltitudeM: 25,
      laneSpacingM: 20,
    });
  });
});

describe('previewRoute', () => {
  it('POSTs the contract to /api/routes/preview and parses the handoff success example', async () => {
    const fetchImpl = fakeFetch(200, handoffSuccess);
    const result = await previewRoute(vertices, { fetchImpl });
    expect(result).toEqual({ ok: true, plan: handoffSuccess });
    const [url, init] = (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe('/api/routes/preview');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual({ searchArea: { vertices } });
  });

  it('parses the handoff error example into an actionable operator message', async () => {
    const result = await previewRoute(vertices, { fetchImpl: fakeFetch(422, handoffError) });
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.error.code).toBe('SEARCH_AREA_OUT_OF_RANGE');
    expect(operatorMessage(result.error).title).toContain('4.8 km away; the limit is 2.0 km');
  });

  it('rejects a 200 response that does not match the contract', async () => {
    const bad = { ...handoffSuccess, route: { waypoints: [{ latitudeDeg: 1 }] } };
    const result = await previewRoute(vertices, { fetchImpl: fakeFetch(200, bad) });
    expect(result).toMatchObject({ ok: false, error: { code: 'UNEXPECTED_RESPONSE' } });
  });

  it('handles FastAPI request-validation errors (detail is a list)', async () => {
    const result = await previewRoute(vertices, {
      fetchImpl: fakeFetch(422, { detail: [{ loc: ['body'], msg: 'field required' }] }),
    });
    expect(result).toMatchObject({ ok: false, error: { code: 'UNEXPECTED_RESPONSE' } });
  });

  it('reports an unreachable backend as a network error', async () => {
    const fetchImpl = vi.fn(async () => {
      throw new TypeError('Failed to fetch');
    }) as unknown as typeof fetch;
    const result = await previewRoute(vertices, { fetchImpl });
    expect(result).toMatchObject({ ok: false, error: { code: 'NETWORK_ERROR' } });
  });
});

describe('operatorMessage', () => {
  it.each([
    ['INVALID_SEARCH_AREA', { reason: 'SELF_INTERSECTION' }, 'edges cross'],
    ['VEHICLE_POSITION_UNAVAILABLE', {}, 'current drone position'],
    ['ROUTE_TOO_LONG', {}, 'longer than the mission allows'],
    ['MISSION_ACTION_NOT_ALLOWED', {}, "isn't allowed"],
  ])('explains %s', (code, details, expected) => {
    expect(operatorMessage({ code, message: '', details }).title).toContain(expected);
  });

  it('falls back to the backend message and shows the code for unknown errors', () => {
    const m = operatorMessage({ code: 'NEW_THING', message: 'Something new.', details: {} });
    expect(m).toEqual({ title: 'Something new.', action: 'Error code: NEW_THING' });
  });
});
