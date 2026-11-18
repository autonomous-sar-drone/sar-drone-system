import { useCallback, useEffect, useRef, useState } from 'react';
import {
  cancelRevisit,
  commenceMission,
  continueMission,
  giveControls,
  pauseMission,
  resumeMission,
  revisitDetection,
  stopMission,
  takeControls,
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
  /** PHASE 2. Request manual ownership; readiness still comes from the backend. */
  takeControls: (executionId: string) => Promise<void>;
  /** PHASE 2. Hand control back to a neutral hold. */
  giveControls: (executionId: string) => Promise<void>;
  /** PHASE 2. Fly to one candidate's stored capture viewpoint, by id only. */
  revisit: (executionId: string, candidateId: string) => Promise<void>;
  /** PHASE 2. Stop a revisit relocation and hold. */
  cancelRevisit: (executionId: string) => Promise<void>;
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
    // The Phase 2 commands go through the same single-flight guard as the rest.
    // Take and Give in particular must never overlap: two requests racing for
    // ownership of the same aircraft is the one case where the loser's response
    // could leave the console showing the wrong side of a handover.
    takeControls: useCallback(
      (executionId) => run('take-controls', (signal) => takeControls(executionId, { signal })),
      [run],
    ),
    giveControls: useCallback(
      (executionId) => run('give-controls', (signal) => giveControls(executionId, { signal })),
      [run],
    ),
    revisit: useCallback(
      (executionId, candidateId) =>
        run('revisit', (signal) => revisitDetection(executionId, candidateId, { signal })),
      [run],
    ),
    cancelRevisit: useCallback(
      (executionId) => run('cancel-revisit', (signal) => cancelRevisit(executionId, { signal })),
      [run],
    ),
    dismissError: useCallback(() => setState(IDLE), []),
  };
}
