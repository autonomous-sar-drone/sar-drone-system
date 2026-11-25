/**
 * Which operator actions are legal right now, and what the aircraft is doing
 * (PHASE 2). Pure, so the whole availability table can be tested directly rather
 * than by rendering and clicking.
 *
 * The rule throughout: availability is derived from the server-owned missionState,
 * flightActivity and holdReason, never from what the operator just clicked. A click
 * may not optimistically claim takeover, holding or arrival.
 */
import type { FlightActivity, HoldReason, MissionState, MissionUpdateData } from '../../types/mission';
import { MISSION_STATE_COPY } from './missionCopy';

export interface ActionState {
  available: boolean;
  /** Short, operator-facing. Null when available. Shown on the disabled control. */
  reason: string | null;
}

const YES: ActionState = { available: true, reason: null };
const no = (reason: string): ActionState => ({ available: false, reason });

export interface MissionActions {
  /** Activity-aware label. Falls back to the existing lifecycle label. */
  label: string;
  /** Existing lifecycle detail line, unchanged. */
  detail: string;
  pause: ActionState;
  resumeSearch: ActionState;
  stop: ActionState;
  takeControls: ActionState;
  giveControls: ActionState;
  cancelRevisit: ActionState;
  /** Mission-side permission only. A candidate still needs revisitAvailable. */
  revisit: ActionState;
  /** Whether the Take/Give slot should show Give rather than Take. */
  manualActive: boolean;
}

const NO_PHASE_2 = 'Needs the updated backend.';
const BUSY_LIFECYCLE = 'Not available while the aircraft is taking off or returning home.';

/**
 * The activity label. Returns null where the table says to keep showing the existing
 * lifecycle status, so the caller falls back rather than inventing new wording for a
 * state that already has some.
 */
export function activityLabel(
  state: MissionState,
  activity: FlightActivity | null,
  holdReason: HoldReason | null,
  revisitNumber: number | null,
): string | null {
  if (state === 'RUNNING' && activity === 'SEARCHING') return 'Searching';
  if (state !== 'PAUSED') return null;
  switch (activity) {
    case 'MANUAL_CONTROL':
      return 'Manual control active';
    case 'HOLDING':
      // Same activity, two very different situations for the operator: a plain hold
      // can rejoin the route, a viewpoint hold is the end of a revisit.
      return holdReason === 'REVISIT_ARRIVAL' ? 'Holding at viewpoint' : 'Holding position';
    case 'PREPARING_REJOIN':
      return 'Preparing to rejoin search';
    case 'REJOINING':
      return 'Rejoining search route';
    case 'REVISITING':
      // The id is not operator-facing. Without a number to show, stay generic rather
      // than printing an opaque identifier.
      return revisitNumber === null ? 'Revisiting a detection' : `Revisiting detection #${revisitNumber}`;
    default:
      return null;
  }
}

export function missionActions(
  mission: MissionUpdateData | null,
  revisitNumber: number | null = null,
): MissionActions {
  const state = mission?.missionState ?? 'IDLE';
  const activity = mission?.flightActivity ?? null;
  const holdReason = mission?.holdReason ?? null;
  const copy = MISSION_STATE_COPY[state];
  const phase2 = activity !== null;
  const manualActive = state === 'PAUSED' && activity === 'MANUAL_CONTROL';

  const base: MissionActions = {
    label: activityLabel(state, activity, holdReason, revisitNumber) ?? copy.label,
    detail: copy.detail,
    pause: no('The search is not running.'),
    resumeSearch: no('The search is not paused.'),
    // Backend Stop accepts RUNNING and PAUSED only; INTERRUPTED has already landed.
    stop: state === 'RUNNING' || state === 'PAUSED' ? YES : no('There is nothing to stop.'),
    takeControls: no(phase2 ? BUSY_LIFECYCLE : NO_PHASE_2),
    giveControls: no('Manual control is not active.'),
    cancelRevisit: no('No revisit is in progress.'),
    revisit: no(phase2 ? BUSY_LIFECYCLE : NO_PHASE_2),
    manualActive,
  };

  if (!phase2) {
    // Phase 1 backend. Keep the existing Pause/Resume/Stop behaviour exactly as it
    // was and offer none of the Phase 2 actions.
    if (state === 'RUNNING') base.pause = YES;
    if (state === 'PAUSED') base.resumeSearch = YES;
    return base;
  }

  if (state === 'RUNNING' && activity === 'SEARCHING') {
    base.pause = YES;
    base.takeControls = YES;
    base.revisit = YES;
    return base;
  }

  if (state === 'PAUSED') {
    switch (activity) {
      case 'MANUAL_CONTROL':
        // No Resume Search here on purpose. Rejoining the route from a hand-flown
        // position is a backend-controlled manoeuvre that starts from a confirmed
        // hold, so control comes back first and the rejoin is a separate decision.
        base.giveControls = YES;
        base.takeControls = no('You already have control.');
        base.resumeSearch = no('Give control back before resuming the search.');
        break;
      case 'HOLDING':
        base.resumeSearch = YES;
        base.takeControls = YES;
        // A viewpoint hold is the end of a revisit, not a place to start another.
        base.revisit = holdReason === 'REVISIT_ARRIVAL' ? no('Already at a viewpoint.') : YES;
        break;
      case 'PREPARING_REJOIN':
        // The table allows a takeover here only if the backend says so, so this is
        // the one row where manualControlReady decides.
        base.takeControls = mission?.manualControlReady
          ? YES
          : no('The aircraft is not ready to hand over control.');
        base.resumeSearch = no('Already rejoining the search.');
        break;
      case 'REJOINING':
        base.takeControls = YES;
        base.resumeSearch = no('Already rejoining the search.');
        break;
      case 'REVISITING':
        base.takeControls = YES;
        base.cancelRevisit = YES;
        base.resumeSearch = no('Cancel the revisit or wait for it to arrive.');
        break;
      default:
        break;
    }
    return base;
  }

  // RETURNING_HOME, PREFLIGHT_CHECK, INTERRUPTED, COMPLETED, IDLE, PLANNING all keep
  // their existing lifecycle behaviour and offer no manual keyboard control.
  return base;
}
