import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { TelemetryReading } from '../../types/telemetry';
import { TelemetryPanel } from './TelemetryPanel';

const reading: TelemetryReading = {
  data: {
    timestamp: '2026-09-30T18:00:00Z',
    latitudeDeg: 30.2291,
    longitudeDeg: -97.7555,
    relativeAltitudeM: 25.04,
    headingDeg: 272,
    groundSpeedMps: 2.04,
  },
  receivedAtMs: 1_000,
  latencyMs: 42,
};

describe('TelemetryPanel', () => {
  it('renders every contract field with units (ST-014)', () => {
    render(<TelemetryPanel reading={reading} status="live" nowMs={1_200} />);
    expect(screen.getByText('30.229100° N')).toBeInTheDocument();
    expect(screen.getByText('97.755500° W')).toBeInTheDocument();
    expect(screen.getByText('25.0 m')).toBeInTheDocument();
    expect(screen.getByText('Above takeoff point')).toBeInTheDocument();
    expect(screen.getByText('272°')).toBeInTheDocument();
    expect(screen.getByText('W')).toBeInTheDocument();
    expect(screen.getByText('2.0 m/s')).toBeInTheDocument();
    expect(screen.getByText('42 ms')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Live telemetry');
  });

  it('shows unknown heading instead of a misleading number', () => {
    const noHeading = { ...reading, data: { ...reading.data, headingDeg: null } };
    render(<TelemetryPanel reading={noHeading} status="live" nowMs={1_200} />);
    expect(screen.getByText('Unknown')).toBeInTheDocument();
    expect(screen.queryByText(/°$/)).not.toBeInTheDocument();
  });

  it('keeps last known values visible and says so when telemetry stops (ST-016)', () => {
    render(<TelemetryPanel reading={reading} status="stale" nowMs={9_000} />);
    expect(screen.getByRole('status')).toHaveTextContent('Telemetry stopped');
    expect(screen.getByRole('status')).toHaveTextContent('Last update 8s ago');
    expect(screen.getByText('30.229100° N')).toBeInTheDocument();
  });

  it('shows an empty-state hint before the first sample', () => {
    render(<TelemetryPanel reading={null} status="waiting" nowMs={0} />);
    expect(screen.getByRole('status')).toHaveTextContent('Connected, no telemetry yet');
    expect(screen.getByText(/appear here once the first telemetry arrives/)).toBeInTheDocument();
  });
});
