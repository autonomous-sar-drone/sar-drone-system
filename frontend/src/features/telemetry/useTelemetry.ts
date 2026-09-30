import { useEffect, useState } from 'react';
import { EventsClient, type SocketState } from '../../api/eventsClient';
import { config } from '../../core/config';
import type { TelemetryReading } from '../../types/telemetry';
import { deriveLinkStatus, type LinkStatus } from './linkStatus';

export interface TelemetryState {
  reading: TelemetryReading | null;
  status: LinkStatus;
  nowMs: number;
}

/** Re-render on an interval so "stale" and "last update" stay current with no new messages. */
function useNow(intervalMs: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

export function useTelemetry(
  url: string = config.eventsUrl,
  staleMs: number = config.telemetryStaleMs,
): TelemetryState {
  const [reading, setReading] = useState<TelemetryReading | null>(null);
  const [socketState, setSocketState] = useState<SocketState>('connecting');
  const nowMs = useNow(500);

  useEffect(() => {
    const client = new EventsClient(url, {
      onSocketState: setSocketState,
      onMessage: (message) => {
        if (message.type !== 'telemetry_update') return;
        const receivedAtMs = Date.now();
        const sentAtMs = Date.parse(message.data.timestamp);
        setReading({
          data: message.data,
          receivedAtMs,
          latencyMs: Number.isNaN(sentAtMs) ? null : receivedAtMs - sentAtMs,
        });
      },
    });
    client.start();
    return () => client.stop();
  }, [url]);

  // Last known values are deliberately kept when the link goes stale or offline (ST-016).
  const status = deriveLinkStatus(socketState, reading?.receivedAtMs ?? null, nowMs, staleMs);
  return { reading, status, nowMs };
}
