import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import type { MapViewProps } from './features/map/MapView';
import type { EventsState } from './features/events/useEvents';
import type { MissionState, MissionUpdateData } from './types/mission';

/**
 * The real hook opens a WebSocket, which jsdom has no server for, so the socket layer is
 * stubbed and `events` stands in for whatever the backend last pushed. Tests mutate it
 * before rendering to put the app in a given mission state.
 */
let events: EventsState;
vi.mock('./features/events/useEvents', () => ({
  useEvents: () => events,
}));

function missionUpdate(missionState: MissionState, extra: Partial<MissionUpdateData> = {}): MissionUpdateData {
  return {
    executionId: 'exec-1',
    planId: 'plan-1',
    missionState,
    activeTarget: null,
    runtimeError: null,
    timestamp: '2026-10-26T16:00:00Z',
    ...extra,
  };
}

beforeEach(() => {
  events = { reading: null, status: 'connecting', nowMs: 0, mission: null };
});

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

describe('live mission controls', () => {
  it('offers only the actions each mission state allows', () => {
    const cases: [MissionState, string[], string[]][] = [
      ['PREFLIGHT_CHECK', [], ['Pause', 'Resume search', 'Stop and return home', 'Continue search']],
      ['RUNNING', ['Pause', 'Stop and return home'], ['Resume search', 'Continue search']],
      ['PAUSED', ['Resume search', 'Stop and return home'], ['Pause']],
      ['INTERRUPTED', ['Continue search'], ['Pause', 'Resume search', 'Stop and return home']],
      ['RETURNING_HOME', [], ['Pause', 'Resume search', 'Stop and return home']],
    ];

    for (const [state, offered, withheld] of cases) {
      events = { ...events, mission: missionUpdate(state) };
      const { unmount } = render(<App />);
      for (const name of offered) {
        expect(controls().getByRole('button', { name }), `${state} offers ${name}`).toBeEnabled();
      }
      for (const name of withheld) {
        expect(controls().queryByRole('button', { name }), `${state} withholds ${name}`).toBeNull();
      }
      unmount();
    }
  });

  it('posts pause to the execution the backend reported', async () => {
    const fetchMock = mockFetch(200, {
      executionId: 'exec-1',
      planId: 'plan-1',
      missionState: 'PAUSED',
    });
    events = { ...events, mission: missionUpdate('RUNNING') };
    render(<App />);

    await act(async () => fireEvent.click(controls().getByRole('button', { name: 'Pause' })));
    const [url] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/missions/exec-1/pause');
  });

  it('shows a rejected command without moving the state the backend owns', async () => {
    mockFetch(409, {
      detail: { code: 'MISSION_ACTION_NOT_ALLOWED', message: 'not allowed', details: {} },
    });
    events = { ...events, mission: missionUpdate('RUNNING') };
    render(<App />);

    await act(async () => fireEvent.click(controls().getByRole('button', { name: 'Pause' })));
    expect(screen.getByRole('alert')).toHaveTextContent("That isn't allowed right now");
    // The socket is the only thing that may change mission state, so the buttons stand still.
    expect(controls().getByRole('button', { name: 'Pause' })).toBeEnabled();
  });

  it('raises a background failure over anything the operator just did', () => {
    events = {
      ...events,
      mission: missionUpdate('INTERRUPTED', {
        runtimeError: { code: 'MISSION_MONITOR_FAILED', message: 'monitor died', details: {} },
      }),
    };
    render(<App />);
    expect(screen.getByRole('alert')).toHaveTextContent('lost track of the mission');
    // A runtime error is the aircraft's problem, not a dismissible notice.
    expect(within(screen.getByRole('alert')).queryByRole('button', { name: 'Dismiss' })).toBeNull();
  });

  it('names the waypoint the drone is flying to', () => {
    events = {
      ...events,
      mission: missionUpdate('RUNNING', {
        activeTarget: {
          latitudeDeg: 30.2291,
          longitudeDeg: -97.7555,
          altitudeM: 25,
          altitudeReference: 'HOME_RELATIVE',
        },
      }),
    };
    render(<App />);
    expect(screen.getByText(/Flying to/)).toBeInTheDocument();
  });
});


describe('new search after completed execution', () => {
  it('clears the previous drawing and allows a fresh preview and commence', async () => {
    const completed = missionUpdate('COMPLETED');
    events = { ...events, mission: completed };
    const nextPlan = { ...plan, planId: 'plan-2' };
    const fetchMock = vi.fn(async (url: string) => {
      if (url === '/api/routes/preview') return new Response(JSON.stringify(nextPlan), { status: 200 });
      if (url === '/api/missions/commence') {
        return new Response(JSON.stringify({
          executionId: 'exec-2', planId: 'plan-2', missionState: 'RUNNING', activeTarget: null,
        }), { status: 200 });
      }
      throw new Error(`Unexpected endpoint: ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<App />);
    expect(screen.getByText('Search complete')).toBeInTheDocument();

    click('Start a new search');
    expect(map()).toHaveAttribute('data-mode', 'drawing');
    expect(map()).toHaveAttribute('data-route-waypoints', '0');
    expect(screen.getByText('Planning')).toBeInTheDocument();
    expect(controls().queryByRole('button', { name: 'Start a new search' })).toBeNull();
    expect(controls().queryByRole('button', { name: 'Commence mission' })).toBeNull();

    click('stub: place square');
    click('stub: click first corner');
    await act(async () => fireEvent.click(controls().getByRole('button', { name: 'Generate mission plan' })));
    expect(controls().getByRole('button', { name: 'Commence mission' })).toBeEnabled();
    expect(screen.getByText('Plan ready')).toBeInTheDocument();

    await act(async () => fireEvent.click(controls().getByRole('button', { name: 'Commence mission' })));
    const calls = fetchMock.mock.calls as unknown as [string, RequestInit][];
    expect(calls.map(([url]) => url)).toEqual(['/api/routes/preview', '/api/missions/commence']);
    expect(JSON.parse(calls[1][1].body as string)).toEqual({ planId: 'plan-2' });
    // The HTTP response isn't authoritative for UI mission state; only WS updates are.
    expect(screen.getByText('Plan ready')).toBeInTheDocument();
  });

  it('ignores only the dismissed completed execution; a new WS execution becomes visible', () => {
    events = { ...events, mission: missionUpdate('COMPLETED') };
    const view = render(<App />);
    click('Start a new search');
    expect(screen.getByText('Planning')).toBeInTheDocument();

    // The old COMPLETED message may be replayed on reconnect. Keep it dismissed.
    view.rerender(<App />);
    expect(controls().queryByRole('button', { name: 'Start a new search' })).toBeNull();

    events = { ...events, mission: missionUpdate('RUNNING', { executionId: 'exec-2', planId: 'plan-2' }) };
    view.rerender(<App />);
    expect(screen.getByText('Searching')).toBeInTheDocument();
    expect(controls().getByRole('button', { name: 'Pause' })).toBeEnabled();
  });

  it('shows action buttons only in states accepted by the backend', () => {
    events = { ...events, mission: missionUpdate('PREFLIGHT_CHECK') };
    const view = render(<App />);
    expect(controls().queryAllByRole('button')).toHaveLength(0);
    events = { ...events, mission: missionUpdate('INTERRUPTED') };
    view.rerender(<App />);
    expect(controls().getAllByRole('button')).toHaveLength(1);
    expect(controls().getByRole('button', { name: 'Continue search' })).toBeEnabled();
  });
});

describe('operator-facing mission copy', () => {
  it('does not imply an operator-requested interruption was unexpected', () => {
    events = { ...events, mission: missionUpdate('INTERRUPTED') };
    render(<App />);
    expect(screen.getByText(/The search was interrupted/)).toBeInTheDocument();
    expect(screen.queryByText(/stopped on its own/)).toBeNull();
  });

  it('does not describe natural RTL as an operator stop', () => {
    events = { ...events, mission: missionUpdate('RETURNING_HOME') };
    render(<App />);
    expect(screen.getByText(/The aircraft is returning home/)).toBeInTheDocument();
    expect(screen.queryByText(/The search was stopped/)).toBeNull();
  });

  it('gives a specific message when auto-takeoff failed to reach altitude', async () => {
    mockFetch(409, {
      detail: {
        code: 'VEHICLE_NOT_READY',
        message: 'The vehicle did not reach the required takeoff altitude in time.',
        details: { targetAltitudeM: 25 },
      },
    });
    events = { ...events, mission: missionUpdate('INTERRUPTED') };
    render(<App />);
    await act(async () => fireEvent.click(controls().getByRole('button', { name: 'Continue search' })));
    expect(screen.getByRole('alert')).toHaveTextContent('did not reach takeoff altitude');
    expect(screen.getByRole('alert')).not.toHaveTextContent('Check that it is connected, armed');
  });
});
