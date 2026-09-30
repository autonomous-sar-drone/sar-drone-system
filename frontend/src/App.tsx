import { TelemetryPanel } from './features/telemetry/TelemetryPanel';
import { useTelemetry } from './features/telemetry/useTelemetry';

export default function App() {
  const { reading, status, nowMs } = useTelemetry();

  return (
    <>
      <header className="app-header">
        <span className="app-header__mark" aria-hidden="true" />
        <h1>SAR operator console</h1>
      </header>
      <main className="app-main">
        <TelemetryPanel reading={reading} status={status} nowMs={nowMs} />
      </main>
    </>
  );
}
