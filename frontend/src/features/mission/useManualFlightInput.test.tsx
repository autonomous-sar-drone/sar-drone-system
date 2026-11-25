import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ManualControlHandlers, ManualReady } from '../../api/manualControlsClient';
import type { FlightActivity, MissionState, MissionUpdateData } from '../../types/mission';
import { type ManualAxes } from './manualIntent';
import { useManualFlightInput, type ManualChannelFactory } from './useManualFlightInput';

function mission(
  missionState: MissionState,
  flightActivity: FlightActivity | null,
  extra: Partial<MissionUpdateData> = {},
): MissionUpdateData {
  return {
    executionId: 'exec-1',
    planId: 'plan-1',
    missionState,
    activeTarget: null,
    interruptionReason: null,
    runtimeError: null,
    timestamp: '2026-11-17T15:00:00.000Z',
    flightActivity,
    holdReason: null,
    activeRevisitCandidateId: null,
    manualControlReady: true,
    ...extra,
  };
}

const MANUAL = mission('PAUSED', 'MANUAL_CONTROL');

const READY: ManualReady = {
  executionId: 'exec-1',
  controlEpoch: 'epoch-1',
  inputMaxAgeMs: 500,
  watchdogMs: 350,
  sendIntervalMs: 100,
};

/** Records what the hook would put on the wire, and lets tests drive the channel. */
class FakeChannel {
  static current: FakeChannel | null = null;
  static created = 0;
  sent: ManualAxes[] = [];
  started = false;
  stopped = false;

  readonly handlers: ManualControlHandlers;

  constructor(handlers: ManualControlHandlers) {
    this.handlers = handlers;
    FakeChannel.current = this;
    FakeChannel.created += 1;
  }

  start = () => {
    this.started = true;
    this.handlers.onState('connecting', null);
  };

  stop = () => {
    this.stopped = true;
  };

  send = (axes: ManualAxes) => {
    this.sent.push(axes);
    return true;
  };

  becomeReady(ready: ManualReady = READY): void {
    this.handlers.onState('open', null);
    this.handlers.onState('ready', ready);
  }

  drop(): void {
    this.handlers.onState('closed', null);
  }

  last(): ManualAxes | undefined {
    return this.sent.at(-1);
  }
}

const factory: ManualChannelFactory = (_url, handlers) => new FakeChannel(handlers);

function Harness({ data }: { data: MissionUpdateData | null }) {
  const manual = useManualFlightInput(data, { url: 'ws://test', createChannel: factory });
  return (
    <div>
      <output data-testid="capturing">{String(manual.capturing)}</output>
      <output data-testid="axes">{JSON.stringify(manual.axes)}</output>
      <output data-testid="held">{manual.held.join(',')}</output>
      <output data-testid="reason">{manual.unavailableReason ?? ''}</output>
      <input aria-label="notes" />
    </div>
  );
}

function setup(data: MissionUpdateData | null = MANUAL) {
  const view = render(<Harness data={data} />);
  return { view, channel: () => FakeChannel.current as FakeChannel };
}

const capturing = () => screen.getByTestId('capturing').textContent === 'true';
const axes = () => JSON.parse(screen.getByTestId('axes').textContent ?? '{}') as ManualAxes;
const held = () => screen.getByTestId('held').textContent;
const reason = () => screen.getByTestId('reason').textContent;

const press = (code: string, init: KeyboardEventInit = {}) =>
  act(() => void fireEvent.keyDown(window, { code, ...init }));
const release = (code: string, init: KeyboardEventInit = {}) =>
  act(() => void fireEvent.keyUp(window, { code, ...init }));

beforeEach(() => {
  FakeChannel.current = null;
  FakeChannel.created = 0;
});

afterEach(() => {
  vi.useRealTimers();
});

describe('when the channel opens', () => {
  it('connects only while the backend reports manual control', () => {
    const { view } = setup(mission('RUNNING', 'SEARCHING'));
    expect(FakeChannel.created).toBe(0);

    view.rerender(<Harness data={MANUAL} />);
    expect(FakeChannel.created).toBe(1);
  });

  it('captures nothing until all three signals agree', () => {
    const { channel } = setup(MANUAL);
    // Socket connected but no manual_ready yet.
    expect(capturing()).toBe(false);
    expect(reason()).toBe('Manual input unavailable. The control channel is not connected.');

    act(() => channel().becomeReady());
    expect(capturing()).toBe(true);
    expect(reason()).toBe('');
  });

  it('waits on the backend flag even with a ready socket', () => {
    const { channel } = setup(mission('PAUSED', 'MANUAL_CONTROL', { manualControlReady: false }));
    act(() => channel().becomeReady());

    expect(capturing()).toBe(false);
    expect(reason()).toBe('Waiting for the aircraft to accept manual input.');
  });

  it('refuses a channel that belongs to another execution', () => {
    const { channel } = setup(MANUAL);
    act(() => channel().becomeReady({ ...READY, executionId: 'exec-2' }));

    expect(capturing()).toBe(false);
    expect(reason()).toContain('different mission');
  });

  it('sends nothing on the wire before readiness', () => {
    const { channel } = setup(MANUAL);
    press('KeyW');

    expect(channel().sent.filter((a) => a.forward !== 0)).toHaveLength(0);
  });
});

describe('capturing keys', () => {
  function ready() {
    const h = setup(MANUAL);
    act(() => h.channel().becomeReady());
    return h;
  }

  it('sends the axis state for each key', () => {
    const { channel } = ready();
    press('KeyW');
    expect(channel().last()).toEqual({ forward: 1, right: 0, up: 0, yaw: 0 });

    press('KeyD');
    expect(channel().last()).toEqual({ forward: 1, right: 1, up: 0, yaw: 0 });

    press('ShiftLeft', { key: 'Shift' });
    expect(channel().last()).toEqual({ forward: 1, right: 1, up: 1, yaw: 0 });

    press('KeyE');
    expect(channel().last()).toEqual({ forward: 1, right: 1, up: 1, yaw: 1 });
  });

  it('keeps the first key of a contested axis until it is released', () => {
    const { channel } = ready();
    press('KeyW');
    press('KeyS');
    expect(axes().forward).toBe(1);

    release('KeyW');
    expect(channel().last()?.forward).toBe(-1);
  });

  it('ignores auto-repeat so press order survives a held key', () => {
    const { channel } = ready();
    press('KeyW');
    press('KeyS');
    const before = channel().sent.length;

    press('KeyS', { repeat: true });
    press('KeyW', { repeat: true });

    expect(channel().sent.length).toBe(before);
    expect(axes().forward).toBe(1);
  });

  it('returns to neutral when every key is released', () => {
    const { channel } = ready();
    press('KeyW');
    press('KeyE');
    release('KeyW');
    release('KeyE');

    expect(channel().last()).toEqual({ forward: 0, right: 0, up: 0, yaw: 0 });
    expect(held()).toBe('');
  });

  it('prevents the page default only for keys it captures', () => {
    ready();
    const captured = new KeyboardEvent('keydown', { code: 'KeyW', cancelable: true, bubbles: true });
    const ignored = new KeyboardEvent('keydown', { code: 'KeyR', cancelable: true, bubbles: true });
    act(() => void window.dispatchEvent(captured));
    act(() => void window.dispatchEvent(ignored));

    expect(captured.defaultPrevented).toBe(true);
    expect(ignored.defaultPrevented).toBe(false);
  });
});

describe('contexts where flight keys must not apply', () => {
  function ready() {
    const h = setup(MANUAL);
    act(() => h.channel().becomeReady());
    return h;
  }

  it('leaves typing in a text field alone', () => {
    const { channel } = ready();
    const field = screen.getByLabelText('notes');
    const before = channel().sent.length;

    act(() => void fireEvent.keyDown(field, { code: 'KeyW' }));

    expect(channel().sent.length).toBe(before);
    expect(held()).toBe('');
  });

  it('still releases a key when focus moved into a field mid-press', () => {
    // Otherwise the key sticks and the aircraft keeps flying.
    const { channel } = ready();
    press('KeyW');
    const field = screen.getByLabelText('notes');
    act(() => void fireEvent.keyUp(field, { code: 'KeyW' }));

    expect(channel().last()?.forward).toBe(0);
  });
});

describe('clearing intent', () => {
  function ready() {
    const h = setup(MANUAL);
    act(() => h.channel().becomeReady());
    return h;
  }

  it('clears on blur, because a keyup off-window never arrives', () => {
    const { channel } = ready();
    press('KeyW');
    act(() => void fireEvent.blur(window));

    expect(channel().last()).toEqual({ forward: 0, right: 0, up: 0, yaw: 0 });
    expect(held()).toBe('');
  });

  it('does not resume flight from a key still held across a blur', () => {
    // Blur clears everything, but the key may still be physically down. The OS then
    // sends repeat keydowns, and treating one as a fresh press would restart the
    // aircraft without the operator touching anything.
    const { channel } = ready();
    press('KeyW');
    act(() => void fireEvent.blur(window));
    const after = channel().sent.length;

    press('KeyW', { repeat: true });

    expect(channel().sent.length).toBe(after);
    expect(held()).toBe('');
    expect(axes().forward).toBe(0);
  });

  it('clears when the tab is hidden', () => {
    const { channel } = ready();
    press('KeyW');
    const spy = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
    act(() => void fireEvent(document, new Event('visibilitychange')));

    expect(channel().last()?.forward).toBe(0);
    spy.mockRestore();
  });

  it('Escape clears intent without handing control back', () => {
    const { channel } = ready();
    press('KeyW');
    press('Escape', { key: 'Escape' });

    expect(channel().last()?.forward).toBe(0);
    expect(held()).toBe('');
    // Still capturing: dropping ownership is a deliberate act, not a reflex.
    expect(capturing()).toBe(true);
  });

  it('clears and stops capturing when readiness is lost', () => {
    const { channel } = ready();
    press('KeyW');
    act(() => channel().drop());

    expect(capturing()).toBe(false);
    expect(held()).toBe('');
    expect(channel().last()).toEqual({ forward: 0, right: 0, up: 0, yaw: 0 });
  });

  it('clears when the backend withdraws manual readiness', () => {
    const { view, channel } = setup(MANUAL);
    act(() => channel().becomeReady());
    press('KeyW');

    view.rerender(
      <Harness data={mission('PAUSED', 'MANUAL_CONTROL', { manualControlReady: false })} />,
    );

    expect(capturing()).toBe(false);
    expect(channel().last()).toEqual({ forward: 0, right: 0, up: 0, yaw: 0 });
  });

  it('clears when Give Controls moves the aircraft back to holding', () => {
    const { view, channel } = setup(MANUAL);
    act(() => channel().becomeReady());
    press('KeyW');
    const fake = channel();

    view.rerender(<Harness data={mission('PAUSED', 'HOLDING')} />);

    expect(fake.last()).toEqual({ forward: 0, right: 0, up: 0, yaw: 0 });
    expect(fake.stopped).toBe(true);
    expect(capturing()).toBe(false);
  });

  it('clears and tears the channel down on unmount', () => {
    const { view, channel } = setup(MANUAL);
    act(() => channel().becomeReady());
    press('KeyW');
    const fake = channel();

    view.unmount();

    expect(fake.last()).toEqual({ forward: 0, right: 0, up: 0, yaw: 0 });
    expect(fake.stopped).toBe(true);
  });

  it('stops listening to the window after unmount', () => {
    const { view, channel } = setup(MANUAL);
    act(() => channel().becomeReady());
    const fake = channel();
    view.unmount();
    const after = fake.sent.length;

    fireEvent.keyDown(window, { code: 'KeyW' });

    expect(fake.sent.length).toBe(after);
  });
});

describe('across a reconnect', () => {
  it('does not replay keys that were held when the link dropped', () => {
    const { channel } = setup(MANUAL);
    act(() => channel().becomeReady());
    press('KeyW');

    act(() => channel().drop());
    act(() => channel().becomeReady({ ...READY, controlEpoch: 'epoch-2' }));

    expect(held()).toBe('');
    expect(axes()).toEqual({ forward: 0, right: 0, up: 0, yaw: 0 });
    expect(capturing()).toBe(true);
  });

  it('accepts a fresh keypress after a long idle without another takeover', () => {
    vi.useFakeTimers();
    const { channel } = setup(MANUAL);
    act(() => channel().becomeReady());

    act(() => void vi.advanceTimersByTime(8 * 60 * 1000));
    press('KeyW');

    expect(capturing()).toBe(true);
    expect(channel().last()?.forward).toBe(1);
  });
});
