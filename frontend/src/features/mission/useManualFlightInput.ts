import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ManualControlClient,
  type ManualChannelState,
  type ManualControlHandlers,
  type ManualReady,
} from '../../api/manualControlsClient';
import { config } from '../../core/config';
import type { MissionUpdateData } from '../../types/mission';
import {
  NEUTRAL_AXES,
  axesFor,
  flightKeyFor,
  isTextEntryContext,
  pressKey,
  releaseKey,
  type FlightKey,
  type ManualAxes,
} from './manualIntent';

/** Just enough of the client for the hook, so tests can pass a fake. */
export interface ManualChannel {
  start: () => void;
  stop: () => void;
  send: (axes: ManualAxes) => boolean;
}

export type ManualChannelFactory = (url: string, handlers: ManualControlHandlers) => ManualChannel;

const defaultFactory: ManualChannelFactory = (url, handlers) =>
  new ManualControlClient(url, handlers);

export interface ManualFlightState {
  /** True only while flight keys are actually being captured. */
  capturing: boolean;
  /** Current operator intent. Neutral whenever nothing is captured. */
  axes: ManualAxes;
  held: FlightKey[];
  channel: ManualChannelState;
  /**
   * Null when manual input is working. Otherwise a short sentence for the operator,
   * because a dead control channel that looks alive is the dangerous case here.
   */
  unavailableReason: string | null;
  /** Escape. Drops intent without handing control back. */
  clearIntent: () => void;
}

export interface ManualFlightOptions {
  url?: string;
  createChannel?: ManualChannelFactory;
  /** Injected in tests. Defaults to the real window. */
  target?: Pick<Window, 'addEventListener' | 'removeEventListener'>;
}

/**
 * Keyboard manual flight (PHASE 2).
 *
 * The hook owns one question: is the operator allowed to fly by hand right now, and
 * if so what are they asking for. It never decides that a takeover happened. Three
 * independent things must all agree before a single key is captured:
 *
 *   1. the backend says the aircraft's activity is MANUAL_CONTROL
 *   2. the backend says manualControlReady
 *   3. the dedicated socket has issued a manual_ready for THIS execution
 *
 * Any one of them dropping clears every held key and sends neutral, which is why a
 * blur, a hidden tab, a socket drop and a Give Controls all end the same way.
 */
export function useManualFlightInput(
  mission: MissionUpdateData | null,
  options: ManualFlightOptions = {},
): ManualFlightState {
  const url = options.url ?? config.manualControlUrl;
  const createChannel = options.createChannel ?? defaultFactory;

  const [channel, setChannel] = useState<ManualChannelState>('closed');
  const [ready, setReady] = useState<ManualReady | null>(null);
  const [held, setHeld] = useState<FlightKey[]>([]);

  const clientRef = useRef<ManualChannel | null>(null);
  const heldRef = useRef<FlightKey[]>([]);

  const executionId = mission?.executionId ?? null;
  const activity = mission?.flightActivity ?? null;
  const backendReady = mission?.manualControlReady === true;

  // Connect only while the backend says the aircraft is under manual control. Holding
  // the channel open the rest of the time would keep a control socket alive during an
  // autonomous search, which is a liability with no benefit.
  const wantsChannel = executionId !== null && activity === 'MANUAL_CONTROL';

  // Derived rather than stored. Once the backend stops reporting manual control the
  // channel is closed whatever the socket last said, so reading it that way removes
  // the window where stale socket state could still look live.
  const channelState: ManualChannelState = wantsChannel ? channel : 'closed';
  const activeReady = wantsChannel ? ready : null;

  const capturing =
    wantsChannel &&
    backendReady &&
    channelState === 'ready' &&
    activeReady !== null &&
    activeReady.executionId === executionId;

  /** Drop all held keys and ask for neutral. Safe to call when nothing is held. */
  const clearIntent = useCallback(() => {
    const hadKeys = heldRef.current.length > 0;
    heldRef.current = [];
    if (hadKeys) setHeld([]);
    // Always attempt neutral, even with nothing held: if a frame was already sent and
    // the channel then recovered, the aircraft could still be acting on it.
    clientRef.current?.send(NEUTRAL_AXES);
  }, []);

  useEffect(() => {
    // No channel wanted: the previous run's cleanup already reset the state, and
    // channelState above covers the first render.
    if (!wantsChannel) return;
    const handlers: ManualControlHandlers = {
      onState: (state, nextReady) => {
        setChannel(state);
        setReady(nextReady);
        // A fresh manual_ready starts a new control epoch. Anything the operator was
        // holding before belonged to the old one and must not carry over.
        if (state !== 'ready') {
          heldRef.current = [];
          setHeld([]);
        }
      },
    };
    const client = createChannel(url, handlers);
    clientRef.current = client;
    client.start();
    return () => {
      // Attempt neutral before tearing down, so an unmount mid-manoeuvre does not
      // leave the last non-zero frame as the most recent thing the backend saw.
      client.send(NEUTRAL_AXES);
      client.stop();
      clientRef.current = null;
      heldRef.current = [];
      setHeld([]);
      setChannel('closed');
      setReady(null);
    };
  }, [wantsChannel, url, createChannel]);

  useEffect(() => {
    if (!capturing) {
      clearIntent();
      return;
    }
    const target = options.target ?? window;

    const apply = (next: FlightKey[]) => {
      heldRef.current = next;
      setHeld(next);
      clientRef.current?.send(axesFor(next));
    };

    const onKeyDown = (event: Event) => {
      const keyEvent = event as KeyboardEvent;
      if (keyEvent.key === 'Escape') {
        // Clears what the operator is asking for, and nothing else. Escape must not
        // hand control back: dropping ownership is a deliberate act, not a reflex.
        if (heldRef.current.length > 0) {
          event.preventDefault();
          clearIntent();
        }
        return;
      }
      const key = flightKeyFor(keyEvent);
      if (key === null) return;
      // Checked only once a flight key is recognized, so typing in a field is never
      // swallowed and nothing else on the page loses its default behaviour.
      if (isTextEntryContext(keyEvent.target)) return;
      // The aircraft is being flown; the page must not also scroll or Quick-Find.
      event.preventDefault();
      // OS key repeat re-fires keydown for an already-held key. Ignoring it keeps the
      // press order intact, which is what decides the winner on a contested axis.
      if (keyEvent.repeat) return;
      const next = pressKey(heldRef.current, key);
      if (next !== heldRef.current) apply(next);
    };

    const onKeyUp = (event: Event) => {
      const keyEvent = event as KeyboardEvent;
      const key = flightKeyFor(keyEvent);
      if (key === null) return;
      // No text-entry check on release: a key pressed while the map had focus must
      // still be released if focus moved into a field mid-press, or it sticks.
      if (!heldRef.current.includes(key)) return;
      event.preventDefault();
      apply(releaseKey(heldRef.current, key));
    };

    // A keyup that happens while the window is not focused never reaches us, so
    // anything that takes focus away has to be treated as a full release.
    const onBlur = () => clearIntent();
    const onVisibility = () => {
      if (document.visibilityState === 'hidden') clearIntent();
    };

    target.addEventListener('keydown', onKeyDown);
    target.addEventListener('keyup', onKeyUp);
    target.addEventListener('blur', onBlur);
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      target.removeEventListener('keydown', onKeyDown);
      target.removeEventListener('keyup', onKeyUp);
      target.removeEventListener('blur', onBlur);
      document.removeEventListener('visibilitychange', onVisibility);
      clearIntent();
    };
  }, [capturing, clearIntent, options.target]);

  const unavailableReason = useMemo(() => {
    if (!wantsChannel) return null;
    if (!backendReady) return 'Waiting for the aircraft to accept manual input.';
    if (channelState !== 'ready') {
      return 'Manual input unavailable. The control channel is not connected.';
    }
    if (activeReady !== null && activeReady.executionId !== executionId) {
      return 'Manual input unavailable. The control channel belongs to a different mission.';
    }
    return null;
  }, [wantsChannel, backendReady, channelState, activeReady, executionId]);

  const axes = useMemo(() => (capturing ? axesFor(held) : NEUTRAL_AXES), [capturing, held]);

  return { capturing, axes, held, channel: channelState, unavailableReason, clearIntent };
}
