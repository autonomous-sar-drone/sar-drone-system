import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useEvents } from './useEvents';
import type { EventMessage } from '../../types/events';
import type { SocketState } from '../../api/eventsClient';
import type { VisionStatus } from '../../types/vision';

/** Captured from the EventsClient the hook constructs, so tests can drive it. */
let deliver: (message: EventMessage) => void;
let setSocketState: (state: SocketState) => void;

vi.mock('../../api/eventsClient', () => ({
  EventsClient: class {
    constructor(_url: string, handlers: {
      onMessage: (m: EventMessage) => void;
      onSocketState: (s: SocketState) => void;
    }) {
      deliver = handlers.onMessage;
      setSocketState = handlers.onSocketState;
    }
    start() {}
    stop() {}
  },
}));

function vision(sessionId: string, revision: number, cameraState: 'LIVE' | 'UNAVAILABLE'): VisionStatus {
  return {
    sessionId,
    revision,
    timestamp: '2026-11-07T15:00:00.000Z',
    camera: { state: cameraState, lastFrameAt: '2026-11-07T15:00:00.000Z' },
    detector: { state: 'UNAVAILABLE', lastInferenceAt: null },
    cameraOutageSeconds: null,
    automaticRtlTriggered: false,
  };
}

const send = (data: VisionStatus) => act(() => deliver({ type: 'vision_status', data }));

beforeEach(() => {
  deliver = () => {};
  setSocketState = () => {};
});

describe('useEvents vision revisions', () => {
  it('ignores an older revision from the same session', () => {
    const { result } = renderHook(() => useEvents());
    send(vision('A', 20, 'LIVE'));
    send(vision('A', 8, 'UNAVAILABLE'));
    // Out of order within one session: the newer snapshot stands.
    expect(result.current.vision?.revision).toBe(20);
    expect(result.current.vision?.camera.state).toBe('LIVE');
  });

  it('accepts a new session even when its revision is lower', () => {
    const { result } = renderHook(() => useEvents());
    send(vision('A', 20, 'LIVE'));
    // A backend restart begins a new session whose revision counts from zero again.
    // Comparing across sessions would pin the panel to the dead session's health.
    send(vision('B', 0, 'UNAVAILABLE'));
    expect(result.current.vision?.sessionId).toBe('B');
    expect(result.current.vision?.camera.state).toBe('UNAVAILABLE');
  });

  it('resumes ordering within the new session', () => {
    const { result } = renderHook(() => useEvents());
    send(vision('A', 20, 'LIVE'));
    send(vision('B', 2, 'UNAVAILABLE'));
    send(vision('B', 1, 'LIVE'));
    expect(result.current.vision?.revision).toBe(2);
    expect(result.current.vision?.camera.state).toBe('UNAVAILABLE');
  });
});

describe('useEvents reconnect generation', () => {
  it('does not count the first connection as a reconnect', () => {
    const { result } = renderHook(() => useEvents());
    act(() => setSocketState('connecting'));
    act(() => setSocketState('open'));
    // The initial load already covers this; counting it would mean two GETs at startup.
    expect(result.current.reconnectGeneration).toBe(0);
    expect(result.current.socketOpen).toBe(true);
  });

  it('counts each re-establishment once', () => {
    const { result } = renderHook(() => useEvents());
    act(() => setSocketState('open'));
    act(() => setSocketState('closed'));
    expect(result.current.socketOpen).toBe(false);

    act(() => setSocketState('connecting'));
    act(() => setSocketState('open'));
    expect(result.current.reconnectGeneration).toBe(1);

    act(() => setSocketState('closed'));
    act(() => setSocketState('open'));
    expect(result.current.reconnectGeneration).toBe(2);
  });

  it('does not count a repeated open as a new reconnect', () => {
    const { result } = renderHook(() => useEvents());
    act(() => setSocketState('open'));
    act(() => setSocketState('closed'));
    act(() => setSocketState('open'));
    act(() => setSocketState('open'));
    expect(result.current.reconnectGeneration).toBe(1);
  });
});
