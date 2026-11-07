import { useEffect, useRef, useState } from 'react';
import { EventsClient, type SocketState } from '../../api/eventsClient';
import { config } from '../../core/config';
import type { MissionUpdateData } from '../../types/mission';
import type { TelemetryReading } from '../../types/telemetry';
import type { VisionStatus } from '../../types/vision';
import type { DetectionUpdateData } from '../../types/detections';
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
  /** Latest vision_status, or null before the first one arrives. */
  vision: VisionStatus | null;
  /** Most recent detection_update. useDetections reconciles these into a list. */
  detection: DetectionUpdateData | null;
  /**
   * Increments on every socket RE-establishment, not the first connect. Consumers
   * holding collections the socket only streams incrementally use this to refetch
   * their authoritative copy: anything that changed while we were disconnected
   * produced updates nobody received.
   */
  reconnectGeneration: number;
  /** True only while the socket is open. */
  socketOpen: boolean;
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
  const [vision, setVision] = useState<VisionStatus | null>(null);
  const [detection, setDetection] = useState<DetectionUpdateData | null>(null);
  const [socketState, setSocketState] = useState<SocketState>('connecting');
  const [reconnectGeneration, setReconnectGeneration] = useState(0);
  const previousSocketState = useRef<SocketState | null>(null);
  const hasOpenedOnce = useRef(false);
  const nowMs = useNow(500);

  useEffect(() => {
    const client = new EventsClient(url, {
      onSocketState: (state) => {
        setSocketState(state);
        // Count re-establishments only. The first open is already covered by the
        // initial load, so counting it would mean two GETs on every startup.
        if (state === 'open' && previousSocketState.current !== 'open') {
          if (hasOpenedOnce.current) setReconnectGeneration((n) => n + 1);
          hasOpenedOnce.current = true;
        }
        previousSocketState.current = state;
      },
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
        } else if (message.type === 'detection_update') {
          // Only the newest is held here; useDetections spots revision gaps and
          // refetches, so an update lost between renders is still recovered.
          setDetection(message.data);
        } else if (message.type === 'vision_status') {
          // revision is monotonic only WITHIN one sessionId. A backend restart opens
          // a new session whose revision starts low again, so comparing revisions
          // across sessions would pin the panel to the dead session's camera health.
          setVision((current) => {
            if (
              current !== null &&
              current.sessionId === message.data.sessionId &&
              message.data.revision < current.revision
            ) {
              return current;
            }
            return message.data;
          });
        }
      },
    });
    client.start();
    return () => client.stop();
  }, [url]);

  // Last known values are deliberately kept when the link goes stale or offline (ST-016).
  const status = deriveLinkStatus(socketState, reading?.receivedAtMs ?? null, nowMs, staleMs);
  return {
    reading, status, nowMs, mission, vision, detection,
    reconnectGeneration, socketOpen: socketState === 'open',
  };
}
