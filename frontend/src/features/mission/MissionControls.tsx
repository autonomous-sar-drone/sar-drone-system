import type { Dispatch, ReactNode } from 'react';
import type { MissionUpdateData } from '../../types/mission';
import { operatorMessage } from '../planning/planErrorCopy';
import {
  canCommence,
  canDeleteSelected,
  canGenerate,
  editMode,
  localIssue,
  type PlanningAction,
  type PlanningState,
} from '../planning/planningState';
import { ManualFlightLegend } from './ManualFlightLegend';
import type { ActionState, MissionActions } from './missionActions';
import { MISSION_STATE_COPY, isLive, missionErrorMessage } from './missionCopy';
import type { ManualFlightState } from './useManualFlightInput';
import type { MissionCommands } from './useMissionCommands';

interface Props {
  planning: PlanningState;
  dispatch: Dispatch<PlanningAction>;
  onGenerate: () => void;
  /** Latest mission_update, or null before any mission has run. */
  mission: MissionUpdateData | null;
  commands: MissionCommands;
  onStartNewSearch: () => void;
  /** PHASE 2. Precomputed availability; see missionActions. */
  actions: MissionActions;
  /** PHASE 2. Keyboard flight state, for the legend. */
  manual: ManualFlightState;
}

const ISSUE_COPY = {
  SELF_INTERSECTION: 'Edges cross, so the plan can’t be generated yet.',
  DEGENERATE_AREA: 'The search area has almost no area.',
  TOO_FEW_VERTICES: 'A search area needs at least three corners.',
  OPEN: 'Close the search area first.',
} as const;

/**
 * One button in the live action row.
 *
 * An action the backend will not accept is rendered disabled with its reason rather
 * than hidden. Hiding it would make the row jump around as the activity changes and
 * leave the operator guessing why something they used a moment ago is gone.
 */
function ActionButton({
  state,
  label,
  busyLabel,
  busy,
  anyPending,
  primary,
  onClick,
}: {
  state: ActionState;
  label: string;
  busyLabel: string;
  busy: boolean;
  anyPending: boolean;
  primary?: boolean;
  onClick: () => void;
}): ReactNode {
  const disabled = !state.available || anyPending;
  return (
    <button
      type="button"
      className={primary ? 'btn btn--primary' : 'btn'}
      disabled={disabled}
      aria-busy={busy}
      // The reason has to reach a screen reader too, not just a mouse hover.
      title={state.reason ?? undefined}
      aria-description={state.reason ?? undefined}
      onClick={onClick}
    >
      {busy ? busyLabel : label}
    </button>
  );
}

/**
 * Controls derive from mission phase + edit mode, not from independent booleans.
 * Once the backend reports a live execution it takes over: the backend owns mission
 * state, so the buttons follow missionState rather than anything computed here.
 */
export function MissionControls({
  planning,
  dispatch,
  onGenerate,
  mission,
  commands,
  onStartNewSearch,
  actions,
  manual,
}: Props) {
  const mode = editMode(planning);
  const { vertices } = planning.searchArea;
  const issue = localIssue(planning);
  const planError = planning.request.status === 'error' ? operatorMessage(planning.request.error) : null;
  const commandError = commands.state.error ? missionErrorMessage(commands.state.error) : null;
  const runtimeError = mission?.runtimeError ? missionErrorMessage(mission.runtimeError) : null;
  const busy = commands.state.pending;

  let buttons: React.ReactNode;
  let note: string | null = null;

  if (mission && isLive(mission.missionState)) {
    const id = mission.executionId;
    note = actions.detail;

    const stopButton = (
      <ActionButton
        state={actions.stop}
        label="Stop and return home"
        busyLabel="Returning home…"
        busy={busy === 'stop'}
        anyPending={busy !== null}
        onClick={() => commands.stop(id)}
      />
    );

    // Take and Give share one slot, always first, so the control that changes who is
    // flying the aircraft never moves out from under the operator's cursor.
    const handoverButton = actions.manualActive ? (
      <ActionButton
        state={actions.giveControls}
        label="Give controls"
        busyLabel="Handing back…"
        busy={busy === 'give-controls'}
        anyPending={busy !== null}
        onClick={() => commands.giveControls(id)}
      />
    ) : (
      <ActionButton
        state={actions.takeControls}
        label="Take controls"
        busyLabel="Taking controls…"
        busy={busy === 'take-controls'}
        anyPending={busy !== null}
        onClick={() => commands.takeControls(id)}
      />
    );

    if (mission.missionState === 'RUNNING' || mission.missionState === 'PAUSED') {
      const resumeOrPause =
        mission.missionState === 'RUNNING' ? (
          <ActionButton
            state={actions.pause}
            label="Pause"
            busyLabel="Pausing…"
            busy={busy === 'pause'}
            anyPending={busy !== null}
            primary
            onClick={() => commands.pause(id)}
          />
        ) : (
          <ActionButton
            state={actions.resumeSearch}
            label="Resume search"
            busyLabel="Resuming…"
            busy={busy === 'resume'}
            anyPending={busy !== null}
            primary
            onClick={() => commands.resume(id)}
          />
        );

      const revisitInProgress =
        mission.flightActivity === 'REVISITING' || mission.activeRevisitCandidateId !== null;

      buttons = (
        <>
          {handoverButton}
          {resumeOrPause}
          {revisitInProgress && (
            <ActionButton
              state={actions.cancelRevisit}
              label="Cancel revisit"
              busyLabel="Cancelling…"
              busy={busy === 'cancel-revisit'}
              anyPending={busy !== null}
              onClick={() => commands.cancelRevisit(id)}
            />
          )}
          {stopButton}
        </>
      );
    } else if (mission.missionState === 'INTERRUPTED') {
      // The aircraft is already landed. Backend Stop accepts RUNNING/PAUSED only.
      buttons = (
        <button
          type="button"
          className="btn btn--primary"
          disabled={busy !== null}
          aria-busy={busy === 'continue'}
          onClick={() => commands.resumeInterrupted(id)}
        >
          {busy === 'continue' ? 'Continuing…' : 'Continue search'}
        </button>
      );
    } else {
      // PREFLIGHT_CHECK and RETURNING_HOME have no backend-supported action here.
      buttons = null;
    }
  } else if (mission?.missionState === 'COMPLETED') {
    note = MISSION_STATE_COPY.COMPLETED.detail;
    buttons = (
      <button type="button" className="btn btn--primary" onClick={onStartNewSearch}>
        Start a new search
      </button>
    );
  } else if (planning.phase === 'IDLE') {
    buttons = (
      <button type="button" className="btn btn--primary" onClick={() => dispatch({ type: 'DEFINE_SEARCH_AREA' })}>
        Define search area
      </button>
    );
    note = 'Draw the area you want the drone to search.';
  } else if (mode === 'drawing') {
    buttons = (
      <>
        <button type="button" className="btn" disabled={vertices.length === 0} onClick={() => dispatch({ type: 'UNDO_LAST_POINT' })}>
          Undo last point
        </button>
        <button type="button" className="btn" disabled={vertices.length === 0} onClick={() => dispatch({ type: 'CLEAR_SEARCH_AREA' })}>
          Clear
        </button>
        <button type="button" className="btn btn--quiet" onClick={() => dispatch({ type: 'CANCEL_PLANNING' })}>
          Cancel
        </button>
      </>
    );
  } else if (mode === 'editing') {
    buttons = (
      <>
        <button type="button" className="btn btn--primary" disabled={!canGenerate(planning)} onClick={onGenerate}>
          Generate mission plan
        </button>
        <button type="button" className="btn" disabled={!canDeleteSelected(planning)} onClick={() => dispatch({ type: 'DELETE_SELECTED_VERTEX' })}>
          Delete corner
        </button>
        <button type="button" className="btn" onClick={() => dispatch({ type: 'CLEAR_SEARCH_AREA' })}>
          Clear
        </button>
        <button type="button" className="btn btn--quiet" onClick={() => dispatch({ type: 'CANCEL_PLANNING' })}>
          Cancel
        </button>
      </>
    );
    if (issue) note = ISSUE_COPY[issue];
    else if (planning.searchArea.selectedIndex !== null && vertices.length <= 3) {
      note = 'A search area needs at least three corners, so this one can’t be deleted.';
    }
  } else if (planning.request.status === 'pending') {
    buttons = (
      <button type="button" className="btn btn--primary" disabled aria-busy="true">
        Generating mission plan…
      </button>
    );
  } else if (planning.plan) {
    const planId = planning.plan.planId;
    buttons = (
      <>
        <button
          type="button"
          className="btn btn--primary"
          disabled={!canCommence(planning) || busy !== null}
          aria-busy={busy === 'commence'}
          onClick={() => commands.commence(planId)}
        >
          {busy === 'commence' ? 'Commencing…' : 'Commence mission'}
        </button>
        <button
          type="button"
          className="btn"
          disabled={busy !== null}
          onClick={() => dispatch({ type: 'EDIT_SEARCH_AREA' })}
        >
          Edit search area
        </button>
      </>
    );
    note = 'Review the route on the map, then commence or edit the search area.';
  }

  // A background failure outranks a rejected command: the aircraft needs attention first.
  const error = runtimeError ?? commandError ?? planError;
  const onDismiss = runtimeError
    ? null
    : commandError
      ? commands.dismissError
      : () => dispatch({ type: 'DISMISS_ERROR' });

  return (
    <div className="controls" style={{ gridArea: 'controls' }}>
      <div className="controls__row" role="toolbar" aria-label="Mission controls">
        {buttons}
        {note && <p className="controls__hint">{note}</p>}
      </div>
      {/* Only while the backend says the aircraft is under manual control, so the
          legend is never on screen when the keys would do nothing. */}
      {actions.manualActive && (
        <ManualFlightLegend
          held={manual.held}
          axes={manual.axes}
          capturing={manual.capturing}
          unavailableReason={manual.unavailableReason}
        />
      )}
      {error && (
        <div className="controls__error" role="alert">
          <div>
            <p className="controls__error-title">{error.title}</p>
            <p className="controls__error-action">{error.action}</p>
          </div>
          {onDismiss && (
            <button type="button" className="btn btn--small" onClick={onDismiss}>
              Dismiss
            </button>
          )}
        </div>
      )}
    </div>
  );
}
