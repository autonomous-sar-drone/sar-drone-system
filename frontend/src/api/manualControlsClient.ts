/**
 * Client for the dedicated /ws/manual-control connection (PHASE 2, PROPOSED).
 *
 * Why a second socket rather than more message types on /ws/events:
 * /ws/events is a one-way broadcast that every panel depends on, and manual input is
 * a high-rate, two-way channel with its own liveness contract. Pushing key intent
 * through the broadcast socket would couple the camera panel's reconnects to the
 * aircraft's control link, and a backlog of input frames would delay telemetry.
 *
 * Nothing MAVLink-shaped is ever built here. The client sends operator INTENT as
 * four integer axes and the backend turns that into vehicle commands.
 */
import type { ManualAxes } from '../features/mission/manualIntent';
import { NEUTRAL_AXES, axesEqual } from '../features/mission/manualIntent';

/**
 * connecting / open  -> transport only, not yet cleared to send
 * ready              -> backend sent manual_ready; input is accepted
 * closed             -> no transport; any previous epoch is dead
 */
export type ManualChannelState = 'connecting' | 'open' | 'ready' | 'closed';

/** The backend's clearance to send, and the timings it wants us to respect. */
export interface ManualReady {
  executionId: string;
  controlEpoch: string;
  /** Backend discards input older than this. Advisory to us; it enforces it. */
  inputMaxAgeMs: number;
  /** Backend neutralizes the aircraft if nothing arrives within this. */
  watchdogMs: number;
  /** How often to repeat unchanged state so the watchdog stays fed. */
  sendIntervalMs: number;
}

export interface ManualAck {
  sequence: number;
  accepted: boolean;
}

export interface ManualControlHandlers {
  onState: (state: ManualChannelState, ready: ManualReady | null) => void;
  onAck?: (ack: ManualAck) => void;
}

export interface ManualControlOptions {
  initialRetryMs?: number;
  maxRetryMs?: number;
  WebSocketImpl?: typeof WebSocket;
  /** Injected in tests so send cadence can be driven by fake timers. */
  now?: () => number;
}

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null;
const isPositive = (v: unknown): v is number =>
  typeof v === 'number' && Number.isFinite(v) && v > 0;

/** Defaults used only when the backend omits a timing. Never invented over a sent value. */
const FALLBACK_SEND_INTERVAL_MS = 100;

export type ManualParseResult =
  | { kind: 'ready'; ready: ManualReady }
  | { kind: 'ack'; ack: ManualAck }
  | { kind: 'unknown'; type: string }
  | { kind: 'invalid'; reason: string };

/** Parse one manual-channel frame. Never throws. */
export function parseManualMessage(raw: string): ManualParseResult {
  let json: unknown;
  try {
    json = JSON.parse(raw);
  } catch {
    return { kind: 'invalid', reason: 'frame is not valid JSON' };
  }
  if (!isObj(json) || typeof json.type !== 'string') {
    return { kind: 'invalid', reason: 'frame has no string "type" discriminator' };
  }
  const data = isObj(json.data) ? json.data : null;

  if (json.type === 'manual_ready') {
    if (data === null) return { kind: 'invalid', reason: 'manual_ready has no data object' };
    if (typeof data.executionId !== 'string' || data.executionId === '') {
      return { kind: 'invalid', reason: 'manual_ready.executionId is missing' };
    }
    // Without an epoch there is nothing to stamp input with, and an un-stamped
    // frame could be replayed against a later takeover. Reject rather than invent.
    if (typeof data.controlEpoch !== 'string' || data.controlEpoch === '') {
      return { kind: 'invalid', reason: 'manual_ready.controlEpoch is missing' };
    }
    return {
      kind: 'ready',
      ready: {
        executionId: data.executionId,
        controlEpoch: data.controlEpoch,
        inputMaxAgeMs: isPositive(data.inputMaxAgeMs) ? data.inputMaxAgeMs : 0,
        watchdogMs: isPositive(data.watchdogMs) ? data.watchdogMs : 0,
        sendIntervalMs: isPositive(data.sendIntervalMs)
          ? data.sendIntervalMs
          : FALLBACK_SEND_INTERVAL_MS,
      },
    };
  }

  if (json.type === 'manual_ack') {
    if (data === null || typeof data.sequence !== 'number') {
      return { kind: 'invalid', reason: 'manual_ack.sequence is missing' };
    }
    return {
      kind: 'ack',
      ack: { sequence: data.sequence, accepted: data.accepted === true },
    };
  }

  return { kind: 'unknown', type: json.type };
}

export class ManualControlClient {
  private socket: WebSocket | null = null;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private repeatTimer: ReturnType<typeof setInterval> | null = null;
  private retryMs: number;
  private stopped = true;
  private ready: ManualReady | null = null;
  private sequence = 0;
  private axes: ManualAxes = NEUTRAL_AXES;
  private readonly initialRetryMs: number;
  private readonly maxRetryMs: number;
  private readonly WebSocketImpl: typeof WebSocket;
  private readonly now: () => number;
  private readonly url: string;
  private readonly handlers: ManualControlHandlers;

  constructor(url: string, handlers: ManualControlHandlers, options: ManualControlOptions = {}) {
    this.url = url;
    this.handlers = handlers;
    this.initialRetryMs = options.initialRetryMs ?? 500;
    this.maxRetryMs = options.maxRetryMs ?? 5000;
    this.retryMs = this.initialRetryMs;
    this.WebSocketImpl = options.WebSocketImpl ?? WebSocket;
    this.now = options.now ?? Date.now;
  }

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    this.connect();
  }

  stop(): void {
    this.stopped = true;
    if (this.retryTimer !== null) clearTimeout(this.retryTimer);
    this.retryTimer = null;
    this.clearReady();
    const socket = this.socket;
    this.socket = null;
    if (socket) {
      socket.onmessage = socket.onclose = socket.onerror = null;
      if (socket.readyState === WebSocket.CONNECTING) {
        socket.onopen = () => socket.close();
      } else {
        socket.onopen = null;
        socket.close();
      }
    }
    this.handlers.onState('closed', null);
  }

  /** Current clearance, or null when input must not be sent. */
  currentReady(): ManualReady | null {
    return this.ready;
  }

  /**
   * Send the complete axis state. Unchanged state is skipped here and covered by the
   * repeat timer instead, so a key held down does not flood the channel.
   * Returns false when the channel is not cleared to send.
   */
  send(axes: ManualAxes): boolean {
    if (this.ready === null) {
      // Remember the intent anyway so nothing is lost mid-frame, but a NEW epoch
      // always starts from neutral (see clearReady), so this can never be replayed
      // into a later takeover.
      this.axes = axes;
      return false;
    }
    // Unchanged state is already covered by the repeat timer, so a held key does not
    // turn into a frame per keydown repeat.
    if (axesEqual(this.axes, axes)) return true;
    this.axes = axes;
    return this.transmit();
  }

  private transmit(): boolean {
    const socket = this.socket;
    const ready = this.ready;
    if (socket === null || ready === null || socket.readyState !== WebSocket.OPEN) return false;
    this.sequence += 1;
    socket.send(
      JSON.stringify({
        type: 'manual_input',
        data: {
          executionId: ready.executionId,
          controlEpoch: ready.controlEpoch,
          sequence: this.sequence,
          clientSentAtMs: this.now(),
          forward: this.axes.forward,
          right: this.axes.right,
          up: this.axes.up,
          yaw: this.axes.yaw,
        },
      }),
    );
    return true;
  }

  private connect(): void {
    this.handlers.onState('connecting', null);
    const socket = new this.WebSocketImpl(this.url);
    this.socket = socket;

    socket.onopen = () => {
      this.retryMs = this.initialRetryMs;
      // Open is NOT cleared to send. The backend has to issue a fresh manual_ready
      // first, which is what stops a reconnect from reusing a dead control epoch.
      this.handlers.onState('open', null);
    };

    socket.onmessage = (event: MessageEvent) => {
      if (typeof event.data !== 'string') return;
      const result = parseManualMessage(event.data);
      if (result.kind === 'ready') {
        this.ready = result.ready;
        // A new epoch starts from a clean slate: sequence from zero and no held
        // intent carried across, so nothing the operator pressed before the drop
        // can act on the aircraft after it.
        this.sequence = 0;
        this.axes = NEUTRAL_AXES;
        this.handlers.onState('ready', result.ready);
        this.transmit();
        this.startRepeating(result.ready.sendIntervalMs);
      } else if (result.kind === 'ack') {
        this.handlers.onAck?.(result.ack);
      } else if (result.kind === 'invalid') {
        console.warn(`[manual-control] dropped malformed message: ${result.reason}`);
      }
    };

    socket.onclose = () => {
      if (this.socket !== socket) return;
      this.socket = null;
      this.clearReady();
      this.handlers.onState('closed', null);
      this.scheduleReconnect();
    };
  }

  /** Repeat unchanged state so the backend watchdog does not neutralize mid-manoeuvre. */
  private startRepeating(intervalMs: number): void {
    this.stopRepeating();
    this.repeatTimer = setInterval(() => {
      this.transmit();
    }, intervalMs > 0 ? intervalMs : FALLBACK_SEND_INTERVAL_MS);
  }

  private stopRepeating(): void {
    if (this.repeatTimer !== null) clearInterval(this.repeatTimer);
    this.repeatTimer = null;
  }

  private clearReady(): void {
    this.stopRepeating();
    this.ready = null;
    this.sequence = 0;
    this.axes = NEUTRAL_AXES;
  }

  private scheduleReconnect(): void {
    if (this.stopped) return;
    const delay = this.retryMs;
    this.retryMs = Math.min(this.retryMs * 2, this.maxRetryMs);
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null;
      if (!this.stopped) this.connect();
    }, delay);
  }
}
