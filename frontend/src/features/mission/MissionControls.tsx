/**
 * Bottom control bar from the dashboard sketch. Buttons stay disabled until the
 * mission slice wires them to the backend (ST-003 start, ST-004 pause/resume/abort).
 */
export function MissionControls() {
  return (
    <div className="controls" style={{ gridArea: 'controls' }} role="toolbar" aria-label="Mission controls">
      <button type="button" className="btn btn--primary" disabled>
        Start mission
      </button>
      <button type="button" className="btn" disabled>
        Pause
      </button>
      <button type="button" className="btn btn--danger" disabled>
        Abort
      </button>
      <p className="controls__hint">Define a search area to enable mission controls.</p>
    </div>
  );
}
