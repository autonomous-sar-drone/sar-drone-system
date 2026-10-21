import type { EventMessage } from '../types/telemetry';
import { parseEvent } from './parseEvent';

export type SocketState = 'connecting' | 'open' | 'closed';

export interface EventsClientHandlers {
  onMessage: (message: EventMessage) => void;
  onSocketState: (state: SocketState) => void;
}

export interface EventsClientOptions {
  /** First reconnect delay. Doubles after each failed attempt. */
  initialRetryMs?: number;
  maxRetryMs?: number;
  /** Injectable for tests. Defaults to the browser WebSocket. */
  WebSocketImpl?: typeof WebSocket;
}

/**
 * Owns the /ws/events connection. Parses and validates every frame, forwards
 * understood messages, ignores message types this build doesn't handle yet,
 * and reconnects with capped exponential backoff until stop() is called.
 */
export class EventsClient {
  private socket: WebSocket | null = null;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private retryMs: number;
  private stopped = true;
  private readonly initialRetryMs: number;
  private readonly maxRetryMs: number;
  private readonly WebSocketImpl: typeof WebSocket;
  private readonly warnedTypes = new Set<string>();
  private readonly url: string;
  private readonly handlers: EventsClientHandlers;

  constructor(url: string, handlers: EventsClientHandlers, options: EventsClientOptions = {}) {
    this.url = url;
    this.handlers = handlers;
    this.initialRetryMs = options.initialRetryMs ?? 500;
    this.maxRetryMs = options.maxRetryMs ?? 5000;
    this.retryMs = this.initialRetryMs;
    this.WebSocketImpl = options.WebSocketImpl ?? WebSocket;
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
    const socket = this.socket;
    this.socket = null;
    if (socket) {
      socket.onmessage = socket.onclose = socket.onerror = null;
      if (socket.readyState === WebSocket.CONNECTING) {
        // Closing mid-handshake logs a browser error (e.g. React StrictMode's dev double-mount).
        socket.onopen = () => socket.close();
      } else {
        socket.onopen = null;
        socket.close();
      }
    }
  }

  private connect(): void {
    this.handlers.onSocketState('connecting');
    const socket = new this.WebSocketImpl(this.url);
    this.socket = socket;

    socket.onopen = () => {
      this.retryMs = this.initialRetryMs;
      this.handlers.onSocketState('open');
    };

    socket.onmessage = (event: MessageEvent) => {
      if (typeof event.data !== 'string') return;
      const result = parseEvent(event.data);
      if (result.kind === 'ok') {
        this.handlers.onMessage(result.message);
      } else if (result.kind === 'invalid') {
        console.warn(`[events] dropped malformed message: ${result.reason}`);
      } else if (!this.warnedTypes.has(result.type)) {
        this.warnedTypes.add(result.type);
        console.info(`[events] ignoring message type not handled yet: ${result.type}`);
      }
    };

    // onerror is always followed by onclose, so reconnect logic lives in onclose only.
    socket.onclose = () => {
      if (this.socket !== socket) return;
      this.socket = null;
      this.handlers.onSocketState('closed');
      this.scheduleReconnect();
    };
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
