import { describe, expect, it } from 'vitest';
import type { ProposedPlan } from '../../types/routes';
import type { LatLon } from '../searchArea/geometry';
import {
  canCommence,
  canDeleteSelected,
  canGenerate,
  editMode,
  initialPlanningState,
  localIssue,
  planningReducer,
  type PlanningAction,
  type PlanningState,
} from './planningState';

const STEP = 0.0001; // ~11 m
const p = (x: number, y: number): LatLon => ({
  latitudeDeg: 30.2291 + y * STEP,
  longitudeDeg: -97.7555 + x * STEP,
});

const plan: ProposedPlan = {
  planId: 'plan-1',
  route: {
    waypoints: [{ ...p(1, 1), altitudeM: 25, altitudeReference: 'HOME_RELATIVE' }],
  },
  metrics: { distanceToStartM: 42.3, routeDistanceM: 615.7 },
  planningUsed: { searchAltitudeM: 25, laneSpacingM: 20 },
};

function run(...actions: PlanningAction[]): PlanningState {
  return actions.reduce(planningReducer, initialPlanningState);
}

const square: PlanningAction[] = [
  { type: 'DEFINE_SEARCH_AREA' },
  { type: 'ADD_VERTEX', point: p(0, 0) },
  { type: 'ADD_VERTEX', point: p(4, 0) },
  { type: 'ADD_VERTEX', point: p(4, 4) },
  { type: 'ADD_VERTEX', point: p(0, 4) },
];
const closedSquare = [...square, { type: 'CLOSE_POLYGON' } as const];
const withPlan = [
  ...closedSquare,
  { type: 'GENERATE_REQUESTED' } as const,
  { type: 'PLAN_RECEIVED', plan } as const,
];

describe('drawing', () => {
  it('starts in IDLE and only draws after Define Search Area', () => {
    expect(run({ type: 'ADD_VERTEX', point: p(0, 0) }).searchArea.vertices).toHaveLength(0);
    expect(editMode(run({ type: 'DEFINE_SEARCH_AREA' }))).toBe('drawing');
  });

  it('refuses a vertex whose segment would cross an earlier segment', () => {
    const s = run(...square.slice(0, 4), { type: 'ADD_VERTEX', point: p(2, -3) });
    expect(s.searchArea.vertices).toHaveLength(3);
  });

  it('undo removes only the newest vertex', () => {
    const s = run(...square, { type: 'UNDO_LAST_POINT' });
    expect(s.searchArea.vertices).toEqual([p(0, 0), p(4, 0), p(4, 4)]);
  });

  it('closes only with three or more vertices and never duplicates the first vertex', () => {
    expect(run(...square.slice(0, 3), { type: 'CLOSE_POLYGON' }).searchArea.closed).toBe(false);
    const s = run(...closedSquare);
    expect(s.searchArea.closed).toBe(true);
    expect(s.searchArea.vertices).toHaveLength(4);
  });

  it('cannot generate while the polygon is open', () => {
    expect(canGenerate(run(...square))).toBe(false);
    expect(localIssue(run(...square))).toBe('OPEN');
    expect(canGenerate(run(...closedSquare))).toBe(true);
  });

  it('clear empties the polygon; cancel returns to IDLE', () => {
    expect(run(...closedSquare, { type: 'CLEAR_SEARCH_AREA' }).searchArea).toEqual({
      vertices: [],
      closed: false,
      selectedIndex: null,
    });
    expect(run(...square, { type: 'CANCEL_PLANNING' })).toEqual(initialPlanningState);
  });
});

describe('editing a closed polygon', () => {
  it('drags vertices and flags a resulting crossing instead of silently accepting it', () => {
    const s = run(...closedSquare, { type: 'MOVE_VERTEX', index: 1, point: p(2, 8) });
    expect(s.searchArea.vertices[1]).toEqual(p(2, 8));
    expect(localIssue(s)).toBe('SELF_INTERSECTION');
    expect(canGenerate(s)).toBe(false);
  });

  it('deletes a selected vertex but never goes below three', () => {
    const fourToThree = run(
      ...closedSquare,
      { type: 'SELECT_VERTEX', index: 2 },
      { type: 'DELETE_SELECTED_VERTEX' },
    );
    expect(fourToThree.searchArea.vertices).toEqual([p(0, 0), p(4, 0), p(0, 4)]);

    const atThree = planningReducer(fourToThree, { type: 'SELECT_VERTEX', index: 0 });
    expect(canDeleteSelected(atThree)).toBe(false);
    expect(planningReducer(atThree, { type: 'DELETE_SELECTED_VERTEX' }).searchArea.vertices).toHaveLength(3);
  });
});

describe('generating and editing a plan', () => {
  it('locks the polygon while the request is pending', () => {
    const pending = run(...closedSquare, { type: 'GENERATE_REQUESTED' });
    expect(editMode(pending)).toBe('locked');
    const moved = planningReducer(pending, { type: 'MOVE_VERTEX', index: 0, point: p(1, 1) });
    expect(moved).toBe(pending);
  });

  it('a received plan sets currentRoute and enables Commence Mission', () => {
    const s = run(...withPlan);
    expect(s.currentRoute).toBe(plan.route);
    expect(canCommence(s)).toBe(true);
    expect(editMode(s)).toBe('locked');
  });

  it('Commence Mission is never available from an unvalidated polygon', () => {
    expect(canCommence(run(...closedSquare))).toBe(false);
    expect(
      canCommence(
        run(...closedSquare, { type: 'GENERATE_REQUESTED' }, {
          type: 'PLAN_FAILED',
          error: { code: 'ROUTE_TOO_LONG', message: 'x', details: {} },
        }),
      ),
    ).toBe(false);
  });

  it('Edit Search Area hides the route immediately and keeps the same polygon editable', () => {
    const before = run(...withPlan);
    const s = planningReducer(before, { type: 'EDIT_SEARCH_AREA' });
    expect(s.currentRoute).toBeNull();
    expect(s.plan).toBeNull();
    expect(s.searchArea.vertices).toEqual(before.searchArea.vertices);
    expect(editMode(s)).toBe('editing');
    expect(canCommence(s)).toBe(false);
  });

  it('regenerating replaces the old plan', () => {
    const replacement = { ...plan, planId: 'plan-2' };
    const s = run(
      ...withPlan,
      { type: 'EDIT_SEARCH_AREA' },
      { type: 'MOVE_VERTEX', index: 2, point: p(6, 6) },
      { type: 'GENERATE_REQUESTED' },
      { type: 'PLAN_RECEIVED', plan: replacement },
    );
    expect(s.plan?.planId).toBe('plan-2');
  });

  it('a backend error is kept until the operator changes the polygon', () => {
    const failed = run(...closedSquare, { type: 'GENERATE_REQUESTED' }, {
      type: 'PLAN_FAILED',
      error: { code: 'SEARCH_AREA_OUT_OF_RANGE', message: 'too far', details: {} },
    });
    expect(failed.request.status).toBe('error');
    expect(editMode(failed)).toBe('editing');
    const edited = planningReducer(failed, { type: 'MOVE_VERTEX', index: 0, point: p(0, 1) });
    expect(edited.request.status).toBe('idle');
  });

  it('ignores a late response after the operator cancelled', () => {
    const cancelledThenLate = planningReducer(
      planningReducer(run(...closedSquare), { type: 'CANCEL_PLANNING' }),
      { type: 'PLAN_RECEIVED', plan },
    );
    expect(cancelledThenLate.currentRoute).toBeNull();
  });
});
