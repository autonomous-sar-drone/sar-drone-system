import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useMissionCommands } from './useMissionCommands';

/**
 * The single-command guard, tested at the hook rather than through the toolbar.
 *
 * The toolbar disables its own buttons while a request is in flight, so a second
 * click cannot get through there. The map popup's Revisit is a different entry
 * point: it is not part of that toolbar and is reachable while a mission command is
 * still running. The guard is what stops two requests racing for one aircraft, and
 * only a direct test can tell whether it is doing anything.
 */

const EXECUTION = { executionId: 'exec-1', planId: 'plan-1', missionState: 'PAUSED' };

const okResponse = () => new Response(JSON.stringify(EXECUTION), { status: 200 });
const conflict = () =>
  new Response(
    JSON.stringify({ detail: { code: 'MISSION_ACTION_NOT_ALLOWED', message: 'no', details: {} } }),
    { status: 409 },
  );

/** A fetch that stays pending until the test releases it. */
function deferredFetch() {
  let release: (value: Response) => void = () => {};
  const pending = new Promise<Response>((resolve) => {
    release = resolve;
  });
  const fn = vi.fn(async (_input: RequestInfo | URL) => pending);
  vi.stubGlobal('fetch', fn);
  return {
    fn,
    urls: () => fn.mock.calls.map(([url]) => String(url)),
    settle: async () => {
      await act(async () => {
        release(okResponse());
        await pending;
      });
    },
  };
}

function stubFetch(factory: () => Response) {
  const fn = vi.fn(async (_input: RequestInfo | URL) => factory());
  vi.stubGlobal('fetch', fn);
  return fn;
}

afterEach(() => vi.unstubAllGlobals());

describe('one command at a time', () => {
  it('drops a second command issued while the first is still in flight', async () => {
    const { fn, urls, settle } = deferredFetch();
    const { result } = renderHook(() => useMissionCommands());

    await act(async () => void result.current.takeControls('exec-1'));
    expect(result.current.state.pending).toBe('take-controls');

    // Reaches the hook from the map popup, not the toolbar, so nothing disabled it.
    await act(async () => void result.current.revisit('exec-1', 'cand-1'));

    expect(fn).toHaveBeenCalledTimes(1);
    expect(urls()[0]).toBe('/api/missions/exec-1/take-controls');
    await settle();
  });

  it('accepts the next command once the first has finished', async () => {
    const { fn, settle } = deferredFetch();
    const { result } = renderHook(() => useMissionCommands());

    await act(async () => void result.current.takeControls('exec-1'));
    await settle();
    expect(result.current.state.pending).toBeNull();

    const second = stubFetch(okResponse);
    await act(async () => result.current.giveControls('exec-1'));

    expect(fn).toHaveBeenCalledTimes(1);
    expect(second).toHaveBeenCalledTimes(1);
  });

  it('names the Phase 2 command in flight so the right button shows busy', async () => {
    const { settle } = deferredFetch();
    const { result } = renderHook(() => useMissionCommands());

    await act(async () => void result.current.cancelRevisit('exec-1'));

    expect(result.current.state.pending).toBe('cancel-revisit');
    await settle();
  });

  it('surfaces a rejection and clears the pending command', async () => {
    stubFetch(conflict);
    const { result } = renderHook(() => useMissionCommands());

    await act(async () => result.current.takeControls('exec-1'));

    expect(result.current.state.pending).toBeNull();
    expect(result.current.state.error?.code).toBe('MISSION_ACTION_NOT_ALLOWED');
  });

  it('releases the guard after a rejection', async () => {
    const fn = stubFetch(conflict);
    const { result } = renderHook(() => useMissionCommands());

    await act(async () => result.current.takeControls('exec-1'));
    await act(async () => result.current.giveControls('exec-1'));

    // A failed command must not wedge the console into accepting nothing further.
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it('routes each Phase 2 command to its own endpoint', async () => {
    const fn = stubFetch(okResponse);
    const { result } = renderHook(() => useMissionCommands());

    await act(async () => result.current.takeControls('exec-1'));
    await act(async () => result.current.giveControls('exec-1'));
    await act(async () => result.current.revisit('exec-1', 'cand-2'));
    await act(async () => result.current.cancelRevisit('exec-1'));

    expect(fn.mock.calls.map(([url]) => String(url))).toEqual([
      '/api/missions/exec-1/take-controls',
      '/api/missions/exec-1/give-controls',
      '/api/missions/exec-1/revisit',
      '/api/missions/exec-1/cancel-revisit',
    ]);
  });
});
