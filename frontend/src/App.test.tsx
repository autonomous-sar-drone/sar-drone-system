import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import type { MapViewProps } from './features/map/MapView';

vi.mock('./features/telemetry/useTelemetry', () => ({
  useTelemetry: () => ({ reading: null, status: 'connecting', nowMs: 0 }),
}));

/** WebGL isn't available in jsdom, so the map is replaced by a stub exposing the same callbacks. */
const SQUARE = [
  { latitudeDeg: 30.229, longitudeDeg: -97.756 },
  { latitudeDeg: 30.229, longitudeDeg: -97.755 },
  { latitudeDeg: 30.23, longitudeDeg: -97.755 },
  { latitudeDeg: 30.23, longitudeDeg: -97.756 },
];
vi.mock('./features/map/MapView', () => ({
  MapView: (p: MapViewProps) => (
    <div data-testid="map-stub" data-mode={p.mode} data-route-waypoints={p.route?.waypoints.length ?? 0}>
      <button onClick={() => SQUARE.forEach((pt) => p.onAddVertex(pt))}>stub: place square</button>
      <button onClick={p.onClose}>stub: click first corner</button>
      <button onClick={() => p.onMoveVertex(1, { latitudeDeg: 30.2305, longitudeDeg: -97.7555 })}>
        stub: drag corner across
      </button>
    </div>
  ),
}));

const plan = {
  planId: 'plan-1',
  route: {
    waypoints: [
      { latitudeDeg: 30.2291, longitudeDeg: -97.7559, altitudeM: 25, altitudeReference: 'HOME_RELATIVE' },
      { latitudeDeg: 30.2291, longitudeDeg: -97.7551, altitudeM: 25, altitudeReference: 'HOME_RELATIVE' },
    ],
  },
  metrics: { distanceToStartM: 42.3, routeDistanceM: 615.7 },
  planningUsed: { searchAltitudeM: 25, laneSpacingM: 20 },
};

function mockFetch(status: number, body: unknown) {
  const fn = vi.fn(async () => new Response(JSON.stringify(body), { status }));
  vi.stubGlobal('fetch', fn);
  return fn;
}

const controls = () => within(screen.getByRole('toolbar', { name: 'Mission controls' }));
const map = () => screen.getByTestId('map-stub');
const click = (name: string | RegExp) => fireEvent.click(screen.getByRole('button', { name }));

async function drawClosedSquare() {
  click('Define search area');
  click('stub: place square');
  click('stub: click first corner');
}

afterEach(() => vi.unstubAllGlobals());

describe('dashboard layout', () => {
  it('renders every panel from the sketch', () => {
    render(<App />);
    for (const name of ['Search map', 'Mission status', 'Drone camera', 'Possible detections']) {
      expect(screen.getByRole('heading', { name })).toBeInTheDocument();
    }
  });
});

describe('search area and route preview flow', () => {
  it('walks IDLE -> drawing -> closed -> plan -> edit, following the locked rules', async () => {
    const fetchMock = mockFetch(200, plan);
    render(<App />);

    expect(controls().queryByRole('button', { name: 'Commence mission' })).toBeNull();
    click('Define search area');
    expect(map()).toHaveAttribute('data-mode', 'drawing');
    expect(controls().getByRole('button', { name: 'Undo last point' })).toBeDisabled();

    click('stub: place square');
    // Open polygon: no generate button offered yet.
    expect(controls().queryByRole('button', { name: 'Generate mission plan' })).toBeNull();

    click('stub: click first corner');
    expect(map()).toHaveAttribute('data-mode', 'editing');
    const generate = controls().getByRole('button', { name: 'Generate mission plan' });
    expect(generate).toBeEnabled();

    await act(async () => fireEvent.click(generate));

    const body = JSON.parse((fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1].body as string);
    expect(body).toEqual({ searchArea: { vertices: SQUARE } });
    expect(map()).toHaveAttribute('data-route-waypoints', '2');
    expect(map()).toHaveAttribute('data-mode', 'locked');
    expect(controls().getByRole('button', { name: 'Commence mission' })).toBeEnabled();
    expect(screen.getByText('2 waypoints, 616 m')).toBeInTheDocument();

    click('Edit search area');
    expect(map()).toHaveAttribute('data-route-waypoints', '0');
    expect(map()).toHaveAttribute('data-mode', 'editing');
    expect(controls().queryByRole('button', { name: 'Commence mission' })).toBeNull();
  });

  it('blocks generation when a drag makes edges cross', async () => {
    render(<App />);
    await drawClosedSquare();
    click('stub: drag corner across');
    expect(controls().getByRole('button', { name: 'Generate mission plan' })).toBeDisabled();
    expect(screen.getByText(/Edges cross, so the plan can/)).toBeInTheDocument();
  });

  it('surfaces a backend rejection even when the local polygon looked valid', async () => {
    mockFetch(422, {
      detail: {
        code: 'SEARCH_AREA_OUT_OF_RANGE',
        message: 'The search area is too far from the current vehicle position.',
        details: { distanceToStartM: 4820.0, maxTransitDistanceM: 2000.0 },
      },
    });
    render(<App />);
    await drawClosedSquare();
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Generate mission plan' })));

    expect(screen.getByRole('alert')).toHaveTextContent('too far from the drone');
    expect(screen.getByRole('alert')).toHaveTextContent('4.8 km away; the limit is 2.0 km');
    expect(map()).toHaveAttribute('data-route-waypoints', '0');
    expect(controls().queryByRole('button', { name: 'Commence mission' })).toBeNull();
    expect(map()).toHaveAttribute('data-mode', 'editing');
  });
});
