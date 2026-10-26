import { useEffect, useState } from 'react';
import { EventsClient, type SocketState } from '../../api/eventsClient';
import { config } from '../../core/config';
import type { MissionUpdateData } from '../../types/mission';
import type { TelemetryReading } from '../../types/telemetry';
import { deriveLinkStatus, type LinkStatus } from '../telemetry/linkStatus';

/**
 * One /ws/events connection feeding every live panel. Telemetry and mission updates
 * share a socket on the backend, so they must share one here too: a second client
 * would open a second connection and double the backend's subscriber bookkeeping.
 */
export interface EventsState {
  reading: TelemetryReading | null;
  status: LinkStatus;
  nowMs: number;
  /** Latest mission_update, or null before the first one arrives. */
  mission: MissionUpdateData | null;
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

export function useEvents(
  url: string = config.eventsUrl,
  staleMs: number = config.telemetryStaleMs,
): EventsState {
  const [reading, setReading] = useState<TelemetryReading | null>(null);
  const [mission, setMission] = useState<MissionUpdateData | null>(null);
  const [socketState, setSocketState] = useState<SocketState>('connecting');
  const nowMs = useNow(500);

  useEffect(() => {
    const client = new EventsClient(url, {
      onSocketState: setSocketState,
      onMessage: (message) => {
        if (message.type === 'telemetry_update') {
          const receivedAtMs = Date.now();
          const sentAtMs = Date.parse(message.data.timestamp);
          setReading({
            data: message.data,
            receivedAtMs,
            latencyMs: Number.isNaN(sentAtMs) ? null : receivedAtMs - sentAtMs,
          });
        } else if (message.type === 'mission_update') {
          // The backend owns mission state; we only ever reflect the newest snapshot.
          setMission(message.data);
        }
      },
    });
    client.start();
    return () => client.stop();
  }, [url]);

  // Last known values are deliberately kept when the link goes stale or offline (ST-016).
  const status = deriveLinkStatus(socketState, reading?.receivedAtMs ?? null, nowMs, staleMs);
  return { reading, status, nowMs, mission };
}
