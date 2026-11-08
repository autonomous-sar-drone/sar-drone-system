import { useCallback, useEffect, useRef, useState } from 'react';
import { EmptyState, Panel } from '../../components/Panel';
import type { MissionUpdateData } from '../../types/mission';
import type { VisionStatus } from '../../types/vision';
import {
  CAMERA_STATE_COPY,
  CAMERA_STREAM_URL,
  DETECTOR_STATE_COPY,
  cameraMessage,
  outageText,
} from './cameraCopy';
import { cameraSafetyBanner } from './cameraSafety';

interface Props {
  /** Latest vision_status, or null before the first one arrives. */
  vision: VisionStatus | null;
  /** Latest mission_update; needed to tell an attempted return from a confirmed one. */
  mission: MissionUpdateData | null;
}

/** Automatic attempts before the operator is asked to retry by hand. */
export const MAX_AUTO_RETRIES = 3;
const RETRY_DELAY_MS = 2000;

/**
 * Live camera feed plus independently reported camera and detector health.
 *
 * Two failures are kept apart here, because they need different responses:
 *
 * - The BACKEND reports the camera UNAVAILABLE. The aircraft has a real problem and
 *   the backend may send it home.
 * - The browser's own MJPEG connection drops while the backend still reports LIVE.
 *   Nothing is wrong with the aircraft; only this viewer needs reconnecting. Saying
 *   "no camera feed" here would be false, and could prompt an unnecessary abort.
 *
 * The <img> is mounted only while the backend reports LIVE. An MJPEG response is a
 * held-open connection that keeps painting its last frame, so leaving it mounted
 * through an outage would show a frozen image that looks live.
 */
export function DroneViewPanel({ vision, mission }: Props) {
  const cameraState = vision?.camera.state ?? null;
  const sessionId = vision?.sessionId ?? '';

  // Changing this remounts the <img> on a fresh URL, so the browser opens a new
  // connection instead of reusing the dead one.
  const [attempt, setAttempt] = useState(0);
  // Nothing renders from the retry count, so it stays a ref rather than driving a render.
  const retriesUsed = useRef(0);
  const [needsManualRetry, setNeedsManualRetry] = useState(false);
  const previousState = useRef<string | null>(null);
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearRetryTimer = useCallback(() => {
    if (retryTimer.current !== null) {
      clearTimeout(retryTimer.current);
      retryTimer.current = null;
    }
  }, []);

  // A fresh LIVE period is a clean slate: new URL, retry budget restored.
  useEffect(() => {
    if (cameraState === 'LIVE' && previousState.current !== 'LIVE') {
      clearRetryTimer();
      setAttempt((n) => n + 1);
      retriesUsed.current = 0;
      setNeedsManualRetry(false);
    }
    previousState.current = cameraState;
  }, [cameraState, clearRetryTimer]);

  useEffect(() => clearRetryTimer, [clearRetryTimer]);

  const handleStreamError = useCallback(() => {
    // The backend still says LIVE, so this is the browser's connection, not the
    // aircraft. Retry a bounded number of times, then hand it to the operator
    // rather than hammering the endpoint in a loop.
    if (retriesUsed.current >= MAX_AUTO_RETRIES) {
      setNeedsManualRetry(true);
      return;
    }
    retriesUsed.current += 1;
    clearRetryTimer();
    retryTimer.current = setTimeout(() => {
      retryTimer.current = null;
      setAttempt((n) => n + 1);
    }, RETRY_DELAY_MS);
  }, [clearRetryTimer]);

  const retryNow = useCallback(() => {
    clearRetryTimer();
    retriesUsed.current = 0;
    setNeedsManualRetry(false);
    setAttempt((n) => n + 1);
  }, [clearRetryTimer]);

  const showStream = cameraState === 'LIVE' && !needsManualRetry;
  const safety = cameraSafetyBanner(vision, mission);
  const outage = outageText(vision?.cameraOutageSeconds ?? null);

  let placeholder: { title: string; detail: string } | null = null;
  if (!showStream) {
    placeholder = needsManualRetry
      ? {
          title: 'Video stream dropped in this browser',
          // Deliberately does not say the camera is down: the backend says it is live.
          detail: 'The camera is still running. Only the video connection to this browser failed.',
        }
      : cameraMessage(vision);
  }

  return (
    <Panel title="Drone camera" area="camera">
      <div className="camera-frame">
        {showStream ? (
          <img
            key={`${sessionId}-${attempt}`}
            className="camera-frame__stream"
            src={`${CAMERA_STREAM_URL}?s=${encodeURIComponent(sessionId)}&r=${attempt}`}
            alt="Live view from the drone camera"
            onError={handleStreamError}
          />
        ) : (
          <EmptyState>
            <strong>{placeholder?.title}</strong>
            {placeholder?.detail && (
              <>
                <br />
                {placeholder.detail}
              </>
            )}
          </EmptyState>
        )}
      </div>

      {needsManualRetry && (
        <div className="camera-frame__retry">
          <button type="button" className="btn btn--small btn--primary" onClick={retryNow}>
            Retry feed
          </button>
        </div>
      )}

      <dl className="status-list">
        <div className="status-list__row">
          <dt>Camera</dt>
          <dd>
            {vision ? CAMERA_STATE_COPY[vision.camera.state] : 'Unknown'}
            {outage && ` · ${outage}`}
          </dd>
        </div>
        <div className="status-list__row">
          <dt>Detector</dt>
          <dd>{vision ? DETECTOR_STATE_COPY[vision.detector.state] : 'Unknown'}</dd>
        </div>
      </dl>

      {safety && (
        <p className="camera-frame__alert" data-tone={safety.tone} role="status">
          {safety.text}
        </p>
      )}
    </Panel>
  );
}
