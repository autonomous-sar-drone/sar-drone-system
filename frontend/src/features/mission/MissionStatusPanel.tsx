import { Panel } from '../../components/Panel';
import type { MissionUpdateData } from '../../types/mission';
import { formatArea, formatMeters } from '../planning/format';
import { editMode, localIssue, type PlanningState } from '../planning/planningState';
import { polygonAreaM2 } from '../searchArea/geometry';
import { MISSION_STATE_COPY } from './missionCopy';

// The backend is authoritative once an execution exists; local phase only fills the gap before.
function missionLabel(s: PlanningState, mission: MissionUpdateData | null): string {
  if (mission) return MISSION_STATE_COPY[mission.missionState].label;
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

interface Props {
  planning: PlanningState;
  mission: MissionUpdateData | null;
}

export function MissionStatusPanel({ planning, mission }: Props) {
  const rows: Array<[string, string]> = [
    ['Mission', missionLabel(planning, mission)],
    ['Search area', areaLabel(planning)],
    ['Route', routeLabel(planning)],
  ];
  if (mission?.activeTarget) {
    const t = mission.activeTarget;
    rows.push([
      'Flying to',
      `${t.latitudeDeg.toFixed(5)}, ${t.longitudeDeg.toFixed(5)} at ${t.altitudeM.toFixed(0)} m`,
    ]);
  }
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
