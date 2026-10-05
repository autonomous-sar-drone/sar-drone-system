import { useMemo } from 'react';
import { DroneViewPanel } from './features/camera/DroneViewPanel';
import { DetectionsPanel } from './features/detections/DetectionsPanel';
import { MapPanel } from './features/map/MapPanel';
import { MissionControls } from './features/mission/MissionControls';
import { MissionStatusPanel } from './features/mission/MissionStatusPanel';
import { usePlanning } from './features/planning/usePlanning';
import { TelemetryPanel } from './features/telemetry/TelemetryPanel';
import { useTelemetry } from './features/telemetry/useTelemetry';
import './dashboard.css';

export default function App() {
  const { reading, status, nowMs } = useTelemetry();
  const { state: planning, dispatch, generate } = usePlanning();

  const lat = reading?.data.latitudeDeg;
  const lon = reading?.data.longitudeDeg;
  const drone = useMemo(
    () => (lat !== undefined && lon !== undefined ? { latitudeDeg: lat, longitudeDeg: lon } : null),
    [lat, lon],
  );

  return (
    <>
      <header className="app-header">
        <span className="app-header__mark" aria-hidden="true" />
        <h1>SAR operator console</h1>
      </header>
      <main className="dashboard">
        <MapPanel planning={planning} dispatch={dispatch} route={planning.currentRoute} drone={drone} />
        <TelemetryPanel reading={reading} status={status} nowMs={nowMs} />
        <MissionStatusPanel planning={planning} />
        <DroneViewPanel />
        <DetectionsPanel />
        <MissionControls planning={planning} dispatch={dispatch} onGenerate={generate} />
      </main>
    </>
  );
}
