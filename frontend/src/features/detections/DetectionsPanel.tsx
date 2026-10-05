import { EmptyState, Panel } from '../../components/Panel';

export function DetectionsPanel() {
  return (
    <Panel title="Possible detections" area="detections">
      <EmptyState>
        No detections yet. When the drone spots a possible person, it shows up here with a snapshot
        and approximate location for you to confirm or dismiss.
      </EmptyState>
    </Panel>
  );
}
