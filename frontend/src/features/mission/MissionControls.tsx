import { useState, type Dispatch } from 'react';
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

interface Props {
  planning: PlanningState;
  dispatch: Dispatch<PlanningAction>;
  onGenerate: () => void;
}

const ISSUE_COPY = {
  SELF_INTERSECTION: 'Edges cross, so the plan can’t be generated yet.',
  DEGENERATE_AREA: 'The search area has almost no area.',
  TOO_FEW_VERTICES: 'A search area needs at least three corners.',
  OPEN: 'Close the search area first.',
} as const;

/** Controls derive from mission phase + edit mode, not from independent booleans. */
export function MissionControls({ planning, dispatch, onGenerate }: Props) {
  const [commenceNotice, setCommenceNotice] = useState(false);
  const mode = editMode(planning);
  const { vertices } = planning.searchArea;
  const issue = localIssue(planning);
  const error = planning.request.status === 'error' ? operatorMessage(planning.request.error) : null;

  let buttons: React.ReactNode;
  let note: string | null = null;

  if (planning.phase === 'IDLE') {
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
    buttons = (
      <>
        <button
          type="button"
          className="btn btn--primary"
          disabled={!canCommence(planning)}
          onClick={() => setCommenceNotice(true)}
        >
          Commence mission
        </button>
        <button
          type="button"
          className="btn"
          onClick={() => {
            setCommenceNotice(false);
            dispatch({ type: 'EDIT_SEARCH_AREA' });
          }}
        >
          Edit search area
        </button>
      </>
    );
    note = commenceNotice
      ? 'Mission execution is the next slice. Nothing was sent to the drone.'
      : 'Review the route on the map, then commence or edit the search area.';
  }

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
          <button type="button" className="btn btn--small" onClick={() => dispatch({ type: 'DISMISS_ERROR' })}>
            Dismiss
          </button>
        </div>
      )}
    </div>
  );
}
