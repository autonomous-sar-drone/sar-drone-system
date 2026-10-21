import { useCallback, useEffect, useReducer, useRef } from 'react';
import { previewRoute } from '../../api/routesClient';
import { canGenerate, initialPlanningState, planningReducer } from './planningState';

export function usePlanning() {
  const [state, dispatch] = useReducer(planningReducer, initialPlanningState);
  const stateRef = useRef(state);
  const inFlight = useRef<AbortController | null>(null);

  useEffect(() => {
    stateRef.current = state;
  });

  useEffect(() => () => inFlight.current?.abort(), []);

  const generate = useCallback(async () => {
    const current = stateRef.current;
    if (!canGenerate(current)) return;
    dispatch({ type: 'GENERATE_REQUESTED' });
    const controller = new AbortController();
    inFlight.current = controller;
    try {
      // planning omitted on purpose: no operator control yet, backend applies its defaults.
      const result = await previewRoute(current.searchArea.vertices, { signal: controller.signal });
      if (result.ok) dispatch({ type: 'PLAN_RECEIVED', plan: result.plan });
      else dispatch({ type: 'PLAN_FAILED', error: result.error });
    } catch {
      // Aborted on unmount; nothing to update.
    } finally {
      if (inFlight.current === controller) inFlight.current = null;
    }
  }, []);

  return { state, dispatch, generate };
}
