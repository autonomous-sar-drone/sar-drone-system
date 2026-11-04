import { useEffect, useRef, useState } from 'react';
import { EmptyState, Panel } from '../../components/Panel';
import type { VisionStatus } from '../../types/vision';
import {
  CAMERA_STATE_COPY,
  CAMERA_STREAM_URL,
  DETECTOR_STATE_COPY,
  cameraMessage,
  outageText,
} from './cameraCopy';

interface Props {
  /** Latest vision_status, or null before the first one arrives. */
  vision: VisionStatus | null;
}

/**
 * Live camera feed plus independently reported camera and detector health.
 *
 * The <img> is mounted only while the backend reports the camera LIVE. An MJPEG
 * response is an open connection that paints its last frame forever, so leaving it
 * mounted through an outage would show a frozen image that looks live. Unmounting
 * drops the connection, and each return to LIVE gets a fresh URL so the browser
 * opens a new one instead of reusing the dead one.
 */
export function DroneViewPanel({ vision }: Props) {
  const cameraState = vision?.camera.state ?? null;
  const sessionId = vision?.sessionId ?? '';

  // Bumped on every transition into LIVE, so the stream URL is never reused.
  const [liveRun, setLiveRun] = useState(0);
  const previousState = useRef<string | null>(null);
  // A stream that errors after mounting should not keep claiming to be live.
  const [streamFailed, setStreamFailed] = useState(false);

  useEffect(() => {
    if (cameraState === 'LIVE' && previousState.current !== 'LIVE') {
      setLiveRun((n) => n + 1);
      setStreamFailed(false);
    }
    previousState.current = cameraState;
  }, [cameraState]);

  const showStream = cameraState === 'LIVE' && !streamFailed;
  const message = streamFailed
    ? {
        title: 'Camera stream dropped',
        detail: 'The backend reports the camera as live but the stream did not load. Retrying on the next status update.',
      }
    : cameraMessage(vision);
  const outage = outageText(vision?.cameraOutageSeconds ?? null);

  return (
    <Panel title="Drone camera" area="camera">
      <div className="camera-frame">
        {showStream ? (
          <img
            key={`${sessionId}-${liveRun}`}
            className="camera-frame__stream"
            src={`${CAMERA_STREAM_URL}?s=${encodeURIComponent(sessionId)}&r=${liveRun}`}
            alt="Live view from the drone camera"
            onError={() => setStreamFailed(true)}
          />
        ) : (
          <EmptyState>
            <strong>{message.title}</strong>
            {message.detail && (
              <>
                <br />
                {message.detail}
              </>
            )}
          </EmptyState>
        )}
      </div>

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

      {vision?.automaticRtlTriggered && (
        <p className="camera-frame__alert" role="alert">
          The camera outage sent the aircraft home automatically.
        </p>
      )}
    </Panel>
  );
}
