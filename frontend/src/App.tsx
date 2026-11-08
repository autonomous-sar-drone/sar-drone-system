import { useCallback, useMemo, useState } from 'react';
import { DroneViewPanel } from './features/camera/DroneViewPanel';
import { DetectionsPanel } from './features/detections/DetectionsPanel';
import { MapPanel } from './features/map/MapPanel';
import { MissionControls } from './features/mission/MissionControls';
import { MissionStatusPanel } from './features/mission/MissionStatusPanel';
import { useMissionCommands } from './features/mission/useMissionCommands';
import { useDetections } from './features/detections/useDetections';
import { usePlanning } from './features/planning/usePlanning';
import { TelemetryPanel } from './features/telemetry/TelemetryPanel';
import { useEvents } from './features/events/useEvents';
import type { MapFocus } from './features/map/MapView';
import type { DetectionCandidate } from './types/detections';
import './dashboard.css';

export default function App() {
  const { reading, status, nowMs, mission, vision, detection, reconnectGeneration, socketOpen } =
    useEvents();
  const { state: planning, dispatch, generate } = usePlanning();
  const commands = useMissionCommands();
  const detections = useDetections(detection, { reconnectGeneration, connected: socketOpen });
  // "Show on map" centres without recording any decision, so it is a request, not state.
  const [mapFocus, setMapFocus] = useState<MapFocus | null>(null);
  const [dismissedCompletedId, setDismissedCompletedId] = useState<string | null>(null);

  // The backend keeps the terminal snapshot until a new preview is requested.
  // Hide only the finished execution the operator chose to leave behind;
  // a subsequent execution with a different id is always shown.
  const visibleMission =
    mission?.missionState === 'COMPLETED' && mission.executionId === dismissedCompletedId
      ? null
      : mission;

  function startNewSearch() {
    if (mission?.missionState !== 'COMPLETED') return;
    setDismissedCompletedId(mission.executionId);
    dispatch({ type: 'START_NEW_SEARCH' });
    commands.dismissError();
  }

  const showOnMap = useCallback((candidate: DetectionCandidate) => {
    const location = candidate.estimatedLocation;
    // No fabricated point: a candidate with no position has nothing to centre on.
    if (location === null) return;
    setMapFocus((current) => ({
      latitudeDeg: location.latitudeDeg,
      longitudeDeg: location.longitudeDeg,
      // Re-requesting the same candidate must still move the map.
      nonce: (current?.nonce ?? 0) + 1,
    }));
  }, []);

  const lat = reading?.data.latitudeDeg;
  const lon = reading?.data.longitudeDeg;
  const heading = reading?.data.headingDeg ?? null;
  const drone = useMemo(
    () =>
      lat !== undefined && lon !== undefined
        ? { latitudeDeg: lat, longitudeDeg: lon, headingDeg: heading }
        : null,
    [lat, lon, heading],
  );

  return (
    <>
      <header className="app-header">
        <span className="app-header__mark" aria-hidden="true" />
        <h1>SAR operator console</h1>
      </header>
      <main className="dashboard">
        <MapPanel
          planning={planning}
          dispatch={dispatch}
          route={planning.currentRoute}
          drone={drone}
          detections={detections.candidates}
          focus={mapFocus}
          onSelectDetection={detections.select}
        />
        <TelemetryPanel reading={reading} status={status} nowMs={nowMs} />
        <MissionStatusPanel planning={planning} mission={visibleMission} />
        <DroneViewPanel vision={vision} mission={visibleMission} />
        <DetectionsPanel detections={detections} nowMs={nowMs} onShowOnMap={showOnMap} />
        <MissionControls
          planning={planning}
          dispatch={dispatch}
          onGenerate={generate}
          mission={visibleMission}
          onStartNewSearch={startNewSearch}
          commands={commands}
        />
      </main>
    </>
  );
}
