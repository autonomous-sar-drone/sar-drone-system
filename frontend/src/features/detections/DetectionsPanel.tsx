import { EmptyState, Panel } from '../../components/Panel';
import type { DetectionCandidate } from '../../types/detections';
import {
  QUALITY_COPY,
  STATUS_COPY,
  confidenceText,
  coordinateText,
  detectionErrorMessage,
  relativeTime,
} from './detectionCopy';
import type { DetectionsState } from './useDetections';

interface Props {
  detections: DetectionsState;
  /** Shared clock from useEvents, so relative times tick without their own timer. */
  nowMs: number;
}

function CandidateRow({
  candidate,
  busy,
  disabled,
  nowMs,
  onReview,
}: {
  candidate: DetectionCandidate;
  busy: boolean;
  disabled: boolean;
  nowMs: number;
  onReview: DetectionsState['review'];
}) {
  const location = candidate.estimatedLocation;
  const pending = candidate.status === 'PENDING';

  return (
    <li className="detection" data-status={candidate.status}>
      <img
        className="detection__snapshot"
        src={candidate.snapshotUrl}
        alt={`Snapshot of possible person ${candidate.candidateNumber}`}
        loading="lazy"
      />
      <div className="detection__body">
        <p className="detection__title">
          Possible person {candidate.candidateNumber}
          <span className="detection__status">{STATUS_COPY[candidate.status]}</span>
        </p>
        <p className="detection__meta">
          {confidenceText(candidate.confidence)} · {relativeTime(candidate.capturedAt, nowMs)}
        </p>
        {location ? (
          <p className="detection__meta">
            {coordinateText(location.latitudeDeg, location.longitudeDeg)}
            <br />
            <span className="detection__quality">{QUALITY_COPY[location.quality]}</span>
          </p>
        ) : (
          // Saying nothing here would imply the position is simply still loading.
          <p className="detection__meta detection__quality">No ground position could be worked out.</p>
        )}

        {pending && (
          <div className="detection__actions">
            <button
              type="button"
              className="btn btn--small btn--primary"
              disabled={disabled}
              aria-busy={busy}
              onClick={() => onReview(candidate.candidateId, 'CONFIRM')}
            >
              Confirm
            </button>
            <button
              type="button"
              className="btn btn--small"
              disabled={disabled}
              onClick={() => onReview(candidate.candidateId, 'DISMISS')}
            >
              Dismiss
            </button>
          </div>
        )}
      </div>
    </li>
  );
}

export function DetectionsPanel({ detections, nowMs }: Props) {
  const { candidates, loading, error, reviewing, reviewError } = detections;
  const problem = reviewError ?? error;

  let content: React.ReactNode;
  if (loading && candidates.length === 0) {
    content = <EmptyState>Loading detections…</EmptyState>;
  } else if (error && candidates.length === 0) {
    content = <EmptyState>{detectionErrorMessage(error).title}</EmptyState>;
  } else if (candidates.length === 0) {
    content = (
      <EmptyState>
        No detections yet. When the drone spots a possible person, it shows up here with a snapshot
        and approximate location for you to confirm or dismiss.
      </EmptyState>
    );
  } else {
    content = (
      <ul className="detection-list">
        {candidates.map((candidate) => (
          <CandidateRow
            key={candidate.candidateId}
            candidate={candidate}
            busy={reviewing === candidate.candidateId}
            // One review at a time: a second decision would race the first.
            disabled={reviewing !== null}
            nowMs={nowMs}
            onReview={detections.review}
          />
        ))}
      </ul>
    );
  }

  const pendingCount = candidates.filter((item) => item.status === 'PENDING').length;

  return (
    <Panel
      title="Possible detections"
      area="detections"
      actions={
        pendingCount > 0 ? (
          <span className="panel__badge">{pendingCount} to review</span>
        ) : undefined
      }
    >
      {content}
      {problem && (
        <div className="controls__error" role="alert">
          <div>
            <p className="controls__error-title">{detectionErrorMessage(problem).title}</p>
            <p className="controls__error-action">{detectionErrorMessage(problem).action}</p>
          </div>
          {reviewError && (
            <button type="button" className="btn btn--small" onClick={detections.dismissReviewError}>
              Dismiss
            </button>
          )}
        </div>
      )}
    </Panel>
  );
}
