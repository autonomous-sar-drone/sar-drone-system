import { EmptyState, Panel } from '../../components/Panel';

export function DroneViewPanel() {
  return (
    <Panel title="Drone camera" area="camera">
      <div className="camera-frame">
        <EmptyState>The live camera feed appears here while a mission is running.</EmptyState>
      </div>
    </Panel>
  );
}
