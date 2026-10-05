import { Panel } from '../../components/Panel';
import { formatArea, formatMeters } from '../planning/format';
import { editMode, localIssue, type PlanningState } from '../planning/planningState';
import { polygonAreaM2 } from '../searchArea/geometry';

function missionLabel(s: PlanningState): string {
  if (s.phase === 'IDLE') return 'Not started';
  if (s.phase !== 'PLANNING') return s.phase.replace('_', ' ').toLowerCase();
  return s.plan ? 'Plan ready' : 'Planning';
}

function areaLabel(s: PlanningState): string {
  const { vertices, closed } = s.searchArea;
  if (s.phase === 'IDLE' || vertices.length === 0) return 'Not defined';
  if (!closed) return `Drawing, ${vertices.length} corner${vertices.length === 1 ? '' : 's'}`;
  if (localIssue(s) === 'SELF_INTERSECTION') return 'Edges cross';
  return `${vertices.length} corners, ${formatArea(polygonAreaM2(vertices))}`;
}

function routeLabel(s: PlanningState): string {
  if (s.request.status === 'pending') return 'Generating…';
  if (s.plan) return `${s.plan.route.waypoints.length} waypoints, ${formatMeters(s.plan.metrics.routeDistanceM)}`;
  if (s.request.status === 'error') return 'Rejected';
  return editMode(s) === 'none' ? 'Not generated' : 'Not generated yet';
}

export function MissionStatusPanel({ planning }: { planning: PlanningState }) {
  const rows: Array<[string, string]> = [
    ['Mission', missionLabel(planning)],
    ['Search area', areaLabel(planning)],
    ['Route', routeLabel(planning)],
  ];
  if (planning.plan) {
    const { metrics, planningUsed } = planning.plan;
    rows.push(
      ['Distance to start', formatMeters(metrics.distanceToStartM)],
      ['Search altitude', `${planningUsed.searchAltitudeM} m above takeoff`],
      ['Lane spacing', `${planningUsed.laneSpacingM} m`],
    );
  } else {
    rows.push(['Coverage', 'No data']);
  }

  return (
    <Panel title="Mission status" area="mission">
      <dl className="status-list">
        {rows.map(([label, value]) => (
          <div key={label} className="status-list__row">
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
    </Panel>
  );
}
