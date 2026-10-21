import type { TelemetryReading } from '../../types/telemetry';
import { formatAge, formatHeading, formatLatitude, formatLongitude } from './format';
import type { LinkStatus } from './linkStatus';
import './telemetry.css';

interface Props {
  reading: TelemetryReading | null;
  status: LinkStatus;
  nowMs: number;
}

const STATUS_COPY: Record<LinkStatus, { title: string; detail: string }> = {
  connecting: { title: 'Connecting to backend', detail: 'Trying to reach the telemetry feed.' },
  waiting: { title: 'Connected, no telemetry yet', detail: 'Check that ArduPilot SITL is running.' },
  live: { title: 'Live telemetry', detail: 'Values update as the aircraft reports them.' },
  stale: {
    title: 'Telemetry stopped',
    detail: 'The backend is connected but no new data is arriving. Values below are last known.',
  },
  offline: {
    title: 'Backend unreachable',
    detail: 'Reconnecting automatically. Values below are last known.',
  },
};

export function TelemetryPanel({ reading, status, nowMs }: Props) {
  const copy = STATUS_COPY[status];
  const d = reading?.data;
  const heading = formatHeading(d ? d.headingDeg : null);
  const isOld = status === 'stale' || status === 'offline';

  return (
    <section className="telemetry" style={{ gridArea: 'telemetry' }} aria-labelledby="telemetry-heading">
      <div className={`link-band link-band--${status}`} role="status" aria-live="polite">
        <span className="link-band__dot" aria-hidden="true" />
        <div>
          <p className="link-band__title">{copy.title}</p>
          <p className="link-band__detail">{copy.detail}</p>
        </div>
        {reading && (
          <p className="link-band__age">
            Last update {formatAge(Math.max(0, nowMs - reading.receivedAtMs))}
          </p>
        )}
      </div>

      <h2 id="telemetry-heading" className="visually-hidden">
        Drone telemetry
      </h2>

      {d ? (
        <dl className={`readouts${isOld ? ' readouts--old' : ''}`}>
          <Readout label="Latitude" value={formatLatitude(d.latitudeDeg)} />
          <Readout label="Longitude" value={formatLongitude(d.longitudeDeg)} />
          <Readout
            label="Altitude"
            value={`${d.relativeAltitudeM.toFixed(1)} m`}
            detail="Above takeoff point"
          />
          <Readout
            label="Heading"
            value={heading.value}
            detail={heading.detail}
            muted={d.headingDeg === null}
          />
          <Readout
            label="Ground speed"
            value={`${d.groundSpeedMps.toFixed(1)} m/s`}
          />
          <Readout
            label="Feed delay"
            value={reading.latencyMs === null ? 'Unknown' : `${Math.max(0, reading.latencyMs)} ms`}
            detail="Backend to browser"
          />
        </dl>
      ) : (
        <p className="readouts-empty">
          Drone position, altitude, heading and speed appear here once the first telemetry arrives.
        </p>
      )}
    </section>
  );
}

function Readout(props: { label: string; value: string; detail?: string; muted?: boolean }) {
  return (
    <div className="readout">
      <dt className="readout__label">{props.label}</dt>
      <dd className={`readout__value${props.muted ? ' readout__value--muted' : ''}`}>{props.value}</dd>
      {props.detail && <dd className="readout__detail">{props.detail}</dd>}
    </div>
  );
}
