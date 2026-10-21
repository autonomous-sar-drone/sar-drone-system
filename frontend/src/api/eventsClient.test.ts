import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { EventsClient, type SocketState } from './eventsClient';

class FakeSocket {
  static instances: FakeSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: unknown }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  url: string;
  constructor(url: string) {
    this.url = url;
    FakeSocket.instances.push(this);
  }
  close() {
    this.closed = true;
  }
  // test helpers
  open() { this.onopen?.(); }
  receive(data: unknown) { this.onmessage?.({ data: typeof data === 'string' ? data : JSON.stringify(data) }); }
  drop() { this.onclose?.(); }
}

const telemetry = {
  type: 'telemetry_update',
  data: {
    timestamp: '2026-09-30T18:00:00Z',
    latitudeDeg: 1, longitudeDeg: 2, relativeAltitudeM: 3, headingDeg: null, groundSpeedMps: 4,
  },
};

function setup() {
  const states: SocketState[] = [];
  const messages: unknown[] = [];
  const client = new EventsClient(
    'ws://test/ws/events',
    { onMessage: (m) => messages.push(m), onSocketState: (s) => states.push(s) },
    { WebSocketImpl: FakeSocket as unknown as typeof WebSocket, initialRetryMs: 100, maxRetryMs: 400 },
  );
  return { client, states, messages };
}

describe('EventsClient', () => {
  beforeEach(() => {
    FakeSocket.instances = [];
    vi.useFakeTimers();
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.spyOn(console, 'info').mockImplementation(() => {});
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('forwards valid telemetry and reports socket state', () => {
    const { client, states, messages } = setup();
    client.start();
    FakeSocket.instances[0].open();
    FakeSocket.instances[0].receive(telemetry);
    expect(states).toEqual(['connecting', 'open']);
    expect(messages).toEqual([telemetry]);
  });

  it('drops malformed frames and unknown types without crashing', () => {
    const { client, messages } = setup();
    client.start();
    const s = FakeSocket.instances[0];
    s.open();
    s.receive('garbage');
    s.receive({ type: 'coverage_update', data: {} });
    s.receive(telemetry);
    expect(messages).toEqual([telemetry]);
  });

  it('reconnects with capped exponential backoff and resets after success', () => {
    const { client } = setup();
    client.start();
    FakeSocket.instances[0].drop();
    vi.advanceTimersByTime(99);
    expect(FakeSocket.instances).toHaveLength(1);
    vi.advanceTimersByTime(1); // 100ms
    expect(FakeSocket.instances).toHaveLength(2);
    FakeSocket.instances[1].drop();
    vi.advanceTimersByTime(200);
    expect(FakeSocket.instances).toHaveLength(3);
    FakeSocket.instances[2].drop();
    vi.advanceTimersByTime(400);
    FakeSocket.instances[3].drop();
    vi.advanceTimersByTime(400); // capped at 400, not 800
    expect(FakeSocket.instances).toHaveLength(5);
    FakeSocket.instances[4].open(); // success resets backoff
    FakeSocket.instances[4].drop();
    vi.advanceTimersByTime(100);
    expect(FakeSocket.instances).toHaveLength(6);
  });

  it('does not reconnect after stop()', () => {
    const { client } = setup();
    client.start();
    const s = FakeSocket.instances[0];
    client.stop();
    expect(s.closed).toBe(true);
    s.drop();
    vi.advanceTimersByTime(10_000);
    expect(FakeSocket.instances).toHaveLength(1);
  });
});
