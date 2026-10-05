import { useState, type Dispatch } from 'react';
import { Panel } from '../../components/Panel';
import type { Route } from '../../types/routes';
import { editMode, localIssue, type PlanningAction, type PlanningState } from '../planning/planningState';
import type { LatLon } from '../searchArea/geometry';
import { MapView } from './MapView';

interface Props {
  planning: PlanningState;
  dispatch: Dispatch<PlanningAction>;
  route: Route | null;
  drone: LatLon | null;
}

function hint(planning: PlanningState): string | null {
  const mode = editMode(planning);
  const n = planning.searchArea.vertices.length;
  if (mode === 'drawing') {
    if (n === 0) return 'Click the map to place the first corner. Drag to pan, scroll to zoom.';
    if (n < 3) return `Keep clicking to add corners (${n} of at least 3).`;
    return 'Click the first corner to close the search area.';
  }
  if (mode === 'editing') {
    if (localIssue(planning) === 'SELF_INTERSECTION') return 'Edges cross. Drag a corner to fix the shape.';
    return 'Drag corners to reshape. Click a corner to select it.';
  }
  return null;
}

export function MapPanel({ planning, dispatch, route, drone }: Props) {
  const [centerRequest, setCenterRequest] = useState(0);
  const mode = editMode(planning);
  const message = hint(planning);

  return (
    <Panel
      title="Search map"
      area="map"
      actions={
        <button
          type="button"
          className="btn btn--small"
          disabled={!drone}
          onClick={() => setCenterRequest((n) => n + 1)}
        >
          Center on drone
        </button>
      }
    >
      <div className="map-wrap">
        <MapView
          vertices={planning.searchArea.vertices}
          closed={planning.searchArea.closed}
          mode={mode}
          selectedIndex={planning.searchArea.selectedIndex}
          shapeInvalid={localIssue(planning) === 'SELF_INTERSECTION'}
          route={route}
          drone={drone}
          centerOnDroneRequest={centerRequest}
          onAddVertex={(point) => dispatch({ type: 'ADD_VERTEX', point })}
          onClose={() => dispatch({ type: 'CLOSE_POLYGON' })}
          onMoveVertex={(index, point) => dispatch({ type: 'MOVE_VERTEX', index, point })}
          onSelectVertex={(index) => dispatch({ type: 'SELECT_VERTEX', index })}
        />
        {message && (
          <p className="map-hint" role="status">
            {message}
          </p>
        )}
      </div>
    </Panel>
  );
}
