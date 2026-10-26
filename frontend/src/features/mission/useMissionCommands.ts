import { useCallback, useEffect, useRef, useState } from 'react';
import {
  commenceMission,
  continueMission,
  pauseMission,
  resumeMission,
  stopMission,
} from '../../api/missionsClient';
import type { MissionCommand, MissionError, MissionExecution } from '../../types/mission';

export interface MissionCommandState {
  /** Which command is in flight, so the right button can show a busy state. */
  pending: MissionCommand | null;
  error: MissionError | null;
}

const IDLE: MissionCommandState = { pending: null, error: null };

export interface MissionCommands {
  state: MissionCommandState;
  commence: (planId: string) => Promise<void>;
  pause: (executionId: string) => Promise<void>;
  resume: (executionId: string) => Promise<void>;
  stop: (executionId: string) => Promise<void>;
  resumeInterrupted: (executionId: string) => Promise<void>;
  dismissError: () => void;
}

/**
 * Issues mission commands over HTTP and tracks the one in flight.
 *
 * Deliberately does not hold mission state. The command response and the
 * mission_update that follows carry the same snapshot, so trusting only the
 * socket keeps one source of truth and avoids the two disagreeing.
 */
export function useMissionCommands(): MissionCommands {
  const [state, setState] = useState<MissionCommandState>(IDLE);
  const inFlight = useRef<AbortController | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      inFlight.current?.abort();
    };
  }, []);

  const run = useCallback(
    async (
      command: MissionCommand,
      call: (signal: AbortSignal) => Promise<
        { ok: true; execution: MissionExecution } | { ok: false; error: MissionError }
      >,
    ) => {
      // One command at a time: the backend rejects overlapping transitions anyway,
      // and a second request would race the first for the same execution.
      if (inFlight.current !== null) return;
      const controller = new AbortController();
      inFlight.current = controller;
      setState({ pending: command, error: null });
      try {
        const result = await call(controller.signal);
        if (!mounted.current) return;
        setState(result.ok ? IDLE : { pending: null, error: result.error });
      } catch {
        // Aborted on unmount; nothing to update.
      } finally {
        if (inFlight.current === controller) inFlight.current = null;
      }
    },
    [],
  );

  return {
    state,
    commence: useCallback(
      (planId) => run('commence', (signal) => commenceMission(planId, { signal })),
      [run],
    ),
    pause: useCallback(
      (executionId) => run('pause', (signal) => pauseMission(executionId, { signal })),
      [run],
    ),
    resume: useCallback(
      (executionId) => run('resume', (signal) => resumeMission(executionId, { signal })),
      [run],
    ),
    stop: useCallback(
      (executionId) => run('stop', (signal) => stopMission(executionId, { signal })),
      [run],
    ),
    resumeInterrupted: useCallback(
      (executionId) => run('continue', (signal) => continueMission(executionId, { signal })),
      [run],
    ),
    dismissError: useCallback(() => setState(IDLE), []),
  };
}
