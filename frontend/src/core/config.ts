/**
 * Centralized frontend configuration. Override any value with a VITE_* variable
 * in frontend/.env.local (git-ignored), never by editing code.
 */

function socketUrl(path: string): string {
  // Same origin as the page. In dev, Vite proxies /ws to the FastAPI backend (see vite.config.ts).
  const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
  return `${protocol}://${window.location.host}${path}`;
}

function positiveNumber(raw: string | undefined, fallback: number): number {
  const value = Number(raw);
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

export const config = {
  eventsUrl: import.meta.env.VITE_EVENTS_URL || socketUrl('/ws/events'),
  /**
   * PHASE 2. Separate from the broadcast socket on purpose; see manualControlsClient.
   */
  manualControlUrl: import.meta.env.VITE_MANUAL_CONTROL_URL || socketUrl('/ws/manual-control'),
  /** No telemetry for longer than this marks the link stale (ST-016). */
  telemetryStaleMs: positiveNumber(import.meta.env.VITE_TELEMETRY_STALE_MS, 3000),
} as const;
