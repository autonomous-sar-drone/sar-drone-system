import type { PlanError, ProposedPlan, Route } from '../../types/routes';
import {
  canAppendVertex,
  canClose,
  searchAreaIssue,
  type LatLon,
  type SearchAreaIssue,
} from '../searchArea/geometry';

/**
 * Mission phases from the handoff (section 6). The backend owns the real mission state; this
 * slice only drives IDLE and PLANNING locally, but the union is complete so later phases don't
 * require reshaping every consumer.
 */
export type MissionPhase =
  | 'IDLE'
  | 'PLANNING'
  | 'PREFLIGHT_CHECK'
  | 'RUNNING'
  | 'PAUSED'
  | 'COMPLETED'
  | 'ABORTED';

export type PlanRequest =
  | { status: 'idle' }
  | { status: 'pending' }
  | { status: 'error'; error: PlanError };

export interface PlanningState {
  phase: MissionPhase;
  searchArea: {
    vertices: LatLon[];
    closed: boolean;
    selectedIndex: number | null;
  };
  /** Backend-owned proposed plan. null whenever the polygon is being edited. */
  plan: ProposedPlan | null;
  /**
   * What the route layer draws. Set from the preview response today; later replaced by
   * route_update messages on /ws/events during RUNNING. Kept separate from `plan` on purpose.
   */
  currentRoute: Route | null;
  request: PlanRequest;
}

export const initialPlanningState: PlanningState = {
  phase: 'IDLE',
  searchArea: { vertices: [], closed: false, selectedIndex: null },
  plan: null,
  currentRoute: null,
  request: { status: 'idle' },
};

export type PlanningAction =
  | { type: 'DEFINE_SEARCH_AREA' }
  | { type: 'ADD_VERTEX'; point: LatLon }
  | { type: 'CLOSE_POLYGON' }
  | { type: 'UNDO_LAST_POINT' }
  | { type: 'CLEAR_SEARCH_AREA' }
  | { type: 'CANCEL_PLANNING' }
  | { type: 'SELECT_VERTEX'; index: number | null }
  | { type: 'MOVE_VERTEX'; index: number; point: LatLon }
  | { type: 'DELETE_SELECTED_VERTEX' }
  | { type: 'GENERATE_REQUESTED' }
  | { type: 'PLAN_RECEIVED'; plan: ProposedPlan }
  | { type: 'PLAN_FAILED'; error: PlanError }
  | { type: 'EDIT_SEARCH_AREA' }
  | { type: 'DISMISS_ERROR' };

/** How the map should treat the polygon right now. */
export type EditMode = 'none' | 'drawing' | 'editing' | 'locked';

export function editMode(state: PlanningState): EditMode {
  if (state.phase !== 'PLANNING') return 'none';
  if (state.plan !== null || state.request.status === 'pending') return 'locked';
  return state.searchArea.closed ? 'editing' : 'drawing';
}

export function localIssue(state: PlanningState): SearchAreaIssue | null {
  return searchAreaIssue(state.searchArea.vertices, state.searchArea.closed);
}

export function canGenerate(state: PlanningState): boolean {
  return editMode(state) === 'editing' && localIssue(state) === null;
}

/** Locked rule: only a backend-validated proposed plan can be commenced. */
export function canCommence(state: PlanningState): boolean {
  return state.phase === 'PLANNING' && state.plan !== null && state.request.status !== 'pending';
}

export function canDeleteSelected(state: PlanningState): boolean {
  const { selectedIndex, vertices } = state.searchArea;
  return editMode(state) === 'editing' && selectedIndex !== null && vertices.length > 3;
}

function withSearchArea(
  state: PlanningState,
  patch: Partial<PlanningState['searchArea']>,
): PlanningState {
  // Any geometry change clears a stale backend error; the operator is fixing it.
  return {
    ...state,
    searchArea: { ...state.searchArea, ...patch },
    request: state.request.status === 'error' ? { status: 'idle' } : state.request,
  };
}

export function planningReducer(state: PlanningState, action: PlanningAction): PlanningState {
  const mode = editMode(state);
  const { vertices } = state.searchArea;

  switch (action.type) {
    case 'DEFINE_SEARCH_AREA':
      if (state.phase !== 'IDLE') return state;
      return { ...initialPlanningState, phase: 'PLANNING' };

    case 'ADD_VERTEX':
      if (mode !== 'drawing' || !canAppendVertex(vertices, action.point)) return state;
      return withSearchArea(state, { vertices: [...vertices, action.point] });

    case 'CLOSE_POLYGON':
      if (mode !== 'drawing' || !canClose(vertices)) return state;
      return withSearchArea(state, { closed: true, selectedIndex: null });

    case 'UNDO_LAST_POINT':
      if (mode !== 'drawing' || vertices.length === 0) return state;
      return withSearchArea(state, { vertices: vertices.slice(0, -1) });

    case 'CLEAR_SEARCH_AREA':
      if (mode !== 'drawing' && mode !== 'editing') return state;
      return withSearchArea(state, { vertices: [], closed: false, selectedIndex: null });

    case 'CANCEL_PLANNING':
      if (state.phase !== 'PLANNING' || state.request.status === 'pending') return state;
      return initialPlanningState;

    case 'SELECT_VERTEX':
      if (mode !== 'editing') return state;
      return { ...state, searchArea: { ...state.searchArea, selectedIndex: action.index } };

    case 'MOVE_VERTEX': {
      // Dragging may create a crossing; that's allowed mid-edit and surfaced via localIssue().
      if (mode !== 'editing' || action.index < 0 || action.index >= vertices.length) return state;
      const next = vertices.slice();
      next[action.index] = action.point;
      return withSearchArea(state, { vertices: next });
    }

    case 'DELETE_SELECTED_VERTEX': {
      if (!canDeleteSelected(state)) return state;
      const index = state.searchArea.selectedIndex as number;
      return withSearchArea(state, {
        vertices: vertices.filter((_, i) => i !== index),
        selectedIndex: null,
      });
    }

    case 'GENERATE_REQUESTED':
      if (!canGenerate(state)) return state;
      return {
        ...state,
        searchArea: { ...state.searchArea, selectedIndex: null },
        request: { status: 'pending' },
      };

    case 'PLAN_RECEIVED':
      if (state.request.status !== 'pending') return state;
      return {
        ...state,
        plan: action.plan,
        currentRoute: action.plan.route,
        request: { status: 'idle' },
      };

    case 'PLAN_FAILED':
      if (state.request.status !== 'pending') return state;
      return { ...state, request: { status: 'error', error: action.error } };

    case 'EDIT_SEARCH_AREA':
      // Locked rule: the old route disappears immediately; the same polygon becomes editable.
      if (state.phase !== 'PLANNING' || state.plan === null) return state;
      return { ...state, plan: null, currentRoute: null, request: { status: 'idle' } };

    case 'DISMISS_ERROR':
      if (state.request.status !== 'error') return state;
      return { ...state, request: { status: 'idle' } };
  }
}
