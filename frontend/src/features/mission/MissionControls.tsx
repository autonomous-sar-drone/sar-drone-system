import type { Dispatch } from 'react';
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
import { MISSION_STATE_COPY, isLive, missionErrorMessage } from './missionCopy';
import type { MissionCommands } from './useMissionCommands';

interface Props {
  planning: PlanningState;
  dispatch: Dispatch<PlanningAction>;
  onGenerate: () => void;
  /** Latest mission_update, or null before any mission has run. */
  mission: MissionUpdateData | null;
  commands: MissionCommands;
}

const ISSUE_COPY = {
  SELF_INTERSECTION: 'Edges cross, so the plan can’t be generated yet.',
  DEGENERATE_AREA: 'The search area has almost no area.',
  TOO_FEW_VERTICES: 'A search area needs at least three corners.',
  OPEN: 'Close the search area first.',
} as const;

/**
 * Controls derive from mission phase + edit mode, not from independent booleans.
 * Once the backend reports a live execution it takes over: the backend owns mission
 * state, so the buttons follow missionState rather than anything computed here.
 */
export function MissionControls({ planning, dispatch, onGenerate, mission, commands }: Props) {
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
    const copy = MISSION_STATE_COPY[mission.missionState];
    note = copy.detail;

    const stopButton = (
      <button
        type="button"
        className="btn"
        disabled={busy !== null}
        aria-busy={busy === 'stop'}
        onClick={() => commands.stop(id)}
      >
        {busy === 'stop' ? 'Returning home…' : 'Stop and return home'}
      </button>
    );

    if (mission.missionState === 'RUNNING') {
      buttons = (
        <>
          <button
            type="button"
            className="btn btn--primary"
            disabled={busy !== null}
            aria-busy={busy === 'pause'}
            onClick={() => commands.pause(id)}
          >
            {busy === 'pause' ? 'Pausing…' : 'Pause'}
          </button>
          {stopButton}
        </>
      );
    } else if (mission.missionState === 'PAUSED') {
      buttons = (
        <>
          <button
            type="button"
            className="btn btn--primary"
            disabled={busy !== null}
            aria-busy={busy === 'resume'}
            onClick={() => commands.resume(id)}
          >
            {busy === 'resume' ? 'Resuming…' : 'Resume search'}
          </button>
          {stopButton}
        </>
      );
    } else if (mission.missionState === 'INTERRUPTED') {
      buttons = (
        <>
          <button
            type="button"
            className="btn btn--primary"
            disabled={busy !== null}
            aria-busy={busy === 'continue'}
            onClick={() => commands.resumeInterrupted(id)}
          >
            {busy === 'continue' ? 'Continuing…' : 'Continue search'}
          </button>
          {stopButton}
        </>
      );
    } else if (mission.missionState === 'PREFLIGHT_CHECK') {
      buttons = stopButton;
    } else {
      // RETURNING_HOME: the aircraft is already coming back, nothing left to command.
      buttons = null;
    }
  } else if (mission?.missionState === 'COMPLETED') {
    note = MISSION_STATE_COPY.COMPLETED.detail;
    buttons = (
      <button type="button" className="btn btn--primary" onClick={() => dispatch({ type: 'CANCEL_PLANNING' })}>
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
