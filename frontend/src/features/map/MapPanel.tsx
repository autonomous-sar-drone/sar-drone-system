import { EmptyState, Panel } from '../../components/Panel';

export function MapPanel() {
  return (
    <Panel title="Search map" area="map">
      <div className="map-placeholder">
        <EmptyState>
          The map goes here. You'll draw the search area on it, preview the generated route, and
          see the drone, covered area, and detections.
        </EmptyState>
      </div>
    </Panel>
  );
}
