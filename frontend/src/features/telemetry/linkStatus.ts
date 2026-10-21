import type { SocketState } from '../../api/eventsClient';

/**
 * What the operator should believe about the telemetry right now.
 * - connecting: no socket yet (first load or reconnecting before any data)
 * - waiting:    socket open, backend has not sent telemetry yet
 * - live:       telemetry is arriving
 * - stale:      socket open but telemetry has gone quiet (stalled sim vs. hovering drone)
 * - offline:    socket is closed; values shown are the last known ones
 */
export type LinkStatus = 'connecting' | 'waiting' | 'live' | 'stale' | 'offline';

export function deriveLinkStatus(
  socketState: SocketState,
  lastReceivedAtMs: number | null,
  nowMs: number,
  staleMs: number,
): LinkStatus {
  if (socketState !== 'open') {
    return lastReceivedAtMs === null && socketState === 'connecting' ? 'connecting' : 'offline';
  }
  if (lastReceivedAtMs === null) return 'waiting';
  return nowMs - lastReceivedAtMs > staleMs ? 'stale' : 'live';
}
