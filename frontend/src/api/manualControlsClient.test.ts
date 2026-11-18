import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  ManualControlClient,
  parseManualMessage,
  type ManualChannelState,
  type ManualReady,
} from './manualControlsClient';
import { NEUTRAL_AXES, axesFor } from '../features/mission/manualIntent';

/** Minimal WebSocket stand-in. jsdom has no server to connect to. */
class FakeSocket {
  static last: FakeSocket | null = null;
  static instances = 0;
  readyState = 0; // CONNECTING
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;

  readonly url: string;

  constructor(url: string) {
    this.url = url;
    FakeSocket.last = this;
    FakeSocket.instances += 1;
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    this.readyState = 3;
  }

  // -- test helpers --
  open(): void {
    this.readyState = 1; // OPEN
    this.onopen?.();
  }

  deliver(message: unknown): void {
    this.onmessage?.({ data: JSON.stringify(message) } as MessageEvent);
  }

  drop(): void {
    this.readyState = 3;
    this.onclose?.();
  }

  frames(): Array<Record<string, unknown>> {
    return this.sent.map((raw) => (JSON.parse(raw) as { data: Record<string, unknown> }).data);
  }
}

const readyMessage = (extra: Record<string, unknown> = {}) => ({
  type: 'manual_ready',
  data: {
    executionId: 'exec-1',
    controlEpoch: 'epoch-1',
    inputMaxAgeMs: 500,
    watchdogMs: 350,
    sendIntervalMs: 100,
    ...extra,
  },
});

interface Harness {
  client: ManualControlClient;
  states: ManualChannelState[];
  readies: Array<ManualReady | null>;
  acks: Array<{ sequence: number; accepted: boolean }>;
  socket: () => FakeSocket;
}

function harness(): Harness {
  const states: ManualChannelState[] = [];
  const readies: Array<ManualReady | null> = [];
  const acks: Array<{ sequence: number; accepted: boolean }> = [];
  const client = new ManualControlClient(
    'ws://test/ws/manual-control',
    {
      onState: (state, ready) => {
        states.push(state);
        readies.push(ready);
      },
      onAck: (ack) => acks.push(ack),
    },
    {
      WebSocketImpl: FakeSocket as unknown as typeof WebSocket,
      now: () => 1_760_000_000_000,
      initialRetryMs: 10,
    },
  );
  return { client, states, readies, acks, socket: () => FakeSocket.last as FakeSocket };
}

beforeEach(() => {
  FakeSocket.last = null;
  FakeSocket.instances = 0;
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('parseManualMessage', () => {
  it('reads a manual_ready with its timings', () => {
    const result = parseManualMessage(JSON.stringify(readyMessage()));
    expect(result).toEqual({
      kind: 'ready',
      ready: {
        executionId: 'exec-1',
        controlEpoch: 'epoch-1',
        inputMaxAgeMs: 500,
        watchdogMs: 350,
        sendIntervalMs: 100,
      },
    });
  });

  it('rejects a readiness with no control epoch', () => {
    // Un-stamped input could be replayed against a later takeover, so there is no
    // safe default to invent here.
    const result = parseManualMessage(
      JSON.stringify({ type: 'manual_ready', data: { executionId: 'exec-1' } }),
    );
    expect(result.kind).toBe('invalid');
  });

  it('falls back to a send interval but never to an epoch', () => {
    const result = parseManualMessage(JSON.stringify(readyMessage({ sendIntervalMs: 0 })));
    expect(result.kind === 'ready' && result.ready.sendIntervalMs).toBe(100);
  });

  it('reads an ack and treats a missing accepted flag as a rejection', () => {
    expect(parseManualMessage(JSON.stringify({ type: 'manual_ack', data: { sequence: 4, accepted: true } })))
      .toEqual({ kind: 'ack', ack: { sequence: 4, accepted: true } });
    expect(parseManualMessage(JSON.stringify({ type: 'manual_ack', data: { sequence: 4 } })))
      .toEqual({ kind: 'ack', ack: { sequence: 4, accepted: false } });
  });

  it('reports unknown types and malformed frames without throwing', () => {
    expect(parseManualMessage(JSON.stringify({ type: 'something_new' })).kind).toBe('unknown');
    expect(parseManualMessage('not json').kind).toBe('invalid');
    expect(parseManualMessage(JSON.stringify({ data: {} })).kind).toBe('invalid');
  });
});

describe('readiness gate', () => {
  it('sends nothing before manual_ready, even once the socket is open', () => {
    const h = harness();
    h.client.start();
    h.socket().open();

    expect(h.client.send(axesFor(['W']))).toBe(false);
    expect(h.socket().sent).toHaveLength(0);
    expect(h.states).toEqual(['connecting', 'open']);
  });

  it('sends a complete neutral state the moment readiness arrives', () => {
    const h = harness();
    h.client.start();
    h.socket().open();
    h.socket().deliver(readyMessage());

    expect(h.states).toEqual(['connecting', 'open', 'ready']);
    const [frame] = h.socket().frames();
    expect(frame).toMatchObject({
      executionId: 'exec-1',
      controlEpoch: 'epoch-1',
      sequence: 1,
      forward: 0,
      right: 0,
      up: 0,
      yaw: 0,
    });
    expect(frame.clientSentAtMs).toBe(1_760_000_000_000);
  });

  it('sends the complete axis state, not a delta', () => {
    const h = harness();
    h.client.start();
    h.socket().open();
    h.socket().deliver(readyMessage());
    h.client.send(axesFor(['W', 'D']));

    const last = h.socket().frames().at(-1);
    expect(last).toMatchObject({ forward: 1, right: 1, up: 0, yaw: 0 });
  });

  it('skips a send that would repeat the current state', () => {
    const h = harness();
    h.client.start();
    h.socket().open();
    h.socket().deliver(readyMessage());
    const before = h.socket().sent.length;

    h.client.send(axesFor(['W']));
    h.client.send(axesFor(['W']));

    // One frame for the change; the repeat timer covers the rest.
    expect(h.socket().sent.length).toBe(before + 1);
  });
});

describe('watchdog repeats', () => {
  it('repeats the current state on the backend-supplied interval', () => {
    const h = harness();
    h.client.start();
    h.socket().open();
    h.socket().deliver(readyMessage({ sendIntervalMs: 100 }));
    h.client.send(axesFor(['W']));
    const before = h.socket().sent.length;

    vi.advanceTimersByTime(350);

    expect(h.socket().sent.length).toBe(before + 3);
    expect(h.socket().frames().at(-1)).toMatchObject({ forward: 1 });
  });

  it('keeps repeating through a long idle so manual stays usable', () => {
    // 7+ minutes of no keypresses must not end manual mode; the next press has to
    // work without another Take Controls.
    const h = harness();
    h.client.start();
    h.socket().open();
    h.socket().deliver(readyMessage());

    vi.advanceTimersByTime(8 * 60 * 1000);
    const idleFrames = h.socket().sent.length;
    expect(idleFrames).toBeGreaterThan(4000);

    expect(h.client.send(axesFor(['W']))).toBe(true);
    expect(h.socket().frames().at(-1)).toMatchObject({ forward: 1 });
  });

  it('stops repeating once the socket drops', () => {
    const h = harness();
    h.client.start();
    h.socket().open();
    h.socket().deliver(readyMessage());
    const socket = h.socket();
    socket.drop();
    const after = socket.sent.length;

    vi.advanceTimersByTime(1000);

    expect(socket.sent.length).toBe(after);
  });
});

describe('control epoch across a reconnect', () => {
  it('refuses to send after a drop until a new manual_ready arrives', () => {
    const h = harness();
    h.client.start();
    h.socket().open();
    h.socket().deliver(readyMessage());
    const first = h.socket();
    first.drop();

    expect(h.client.currentReady()).toBeNull();
    expect(h.client.send(axesFor(['W']))).toBe(false);
    expect(h.states.at(-1)).toBe('closed');

    // Reconnect: open alone is still not clearance.
    vi.advanceTimersByTime(20);
    expect(FakeSocket.instances).toBe(2);
    h.socket().open();
    expect(h.client.send(axesFor(['W']))).toBe(false);
    expect(h.socket().sent).toHaveLength(0);
  });

  it('never reuses the old epoch, and restarts the sequence', () => {
    const h = harness();
    h.client.start();
    h.socket().open();
    h.socket().deliver(readyMessage());
    h.client.send(axesFor(['W']));
    expect(h.socket().frames().at(-1)).toMatchObject({ controlEpoch: 'epoch-1', sequence: 2 });

    h.socket().drop();
    vi.advanceTimersByTime(20);
    h.socket().open();
    h.socket().deliver(readyMessage({ controlEpoch: 'epoch-2' }));

    const frame = h.socket().frames().at(-1);
    expect(frame).toMatchObject({ controlEpoch: 'epoch-2', sequence: 1 });
  });

  it('starts the new epoch from neutral rather than replaying held keys', () => {
    // Keys held when the link died belong to the dead epoch. Replaying them would
    // move the aircraft on reconnect without the operator touching anything.
    const h = harness();
    h.client.start();
    h.socket().open();
    h.socket().deliver(readyMessage());
    h.client.send(axesFor(['W', 'UP']));

    h.socket().drop();
    vi.advanceTimersByTime(20);
    h.socket().open();
    h.socket().deliver(readyMessage({ controlEpoch: 'epoch-2' }));

    expect(h.socket().frames()[0]).toMatchObject({ forward: 0, right: 0, up: 0, yaw: 0 });
  });
});

describe('acks and teardown', () => {
  it('forwards acks to the caller', () => {
    const h = harness();
    h.client.start();
    h.socket().open();
    h.socket().deliver(readyMessage());
    h.socket().deliver({ type: 'manual_ack', data: { sequence: 1, accepted: true } });

    expect(h.acks).toEqual([{ sequence: 1, accepted: true }]);
  });

  it('clears readiness and reports closed on stop', () => {
    const h = harness();
    h.client.start();
    h.socket().open();
    h.socket().deliver(readyMessage());
    h.client.stop();

    expect(h.client.currentReady()).toBeNull();
    expect(h.states.at(-1)).toBe('closed');
    expect(h.client.send(axesFor(['W']))).toBe(false);
  });

  it('does not reconnect after stop', () => {
    const h = harness();
    h.client.start();
    h.socket().open();
    h.client.stop();
    const instances = FakeSocket.instances;

    vi.advanceTimersByTime(5000);

    expect(FakeSocket.instances).toBe(instances);
  });

  it('remembers neutral intent while closed without sending it', () => {
    const h = harness();
    h.client.start();
    expect(h.client.send(NEUTRAL_AXES)).toBe(false);
    expect(FakeSocket.last?.sent ?? []).toHaveLength(0);
  });
});
