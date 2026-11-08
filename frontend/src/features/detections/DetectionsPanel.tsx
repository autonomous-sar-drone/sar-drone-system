import { useEffect, useRef } from 'react';
import { EmptyState, Panel } from '../../components/Panel';
import type { DetectionCandidate } from '../../types/detections';
import {
  QUALITY_COPY,
  confidenceText,
  coordinateText,
  detectionErrorMessage,
  locationIsPersonPosition,
  relativeTime,
} from './detectionCopy';
import type { DetectionsState } from './useDetections';

interface Props {
  detections: DetectionsState;
  /** Shared clock from useEvents, so relative times tick without their own timer. */
  nowMs: number;
  /** Centers the map on a candidate without recording any decision. */
  onShowOnMap: (candidate: DetectionCandidate) => void;
}

/** One card in the horizontal queue. Selecting never changes review status. */
function QueueCard({
  candidate,
  selected,
  onSelect,
}: {
  candidate: DetectionCandidate;
  selected: boolean;
  onSelect: () => void;
}) {
  const ref = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!selected) return;
    // Earlier/Later has to move the strip, not just the highlight. Guarded because
    // jsdom does not implement scrollIntoView.
    ref.current?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' });
  }, [selected]);

  return (
    <li className="queue__item">
      <button
        ref={ref}
        type="button"
        className="queue__card"
        aria-pressed={selected}
        aria-label={`Possible person ${candidate.candidateNumber}`}
        onClick={onSelect}
      >
        <img className="queue__thumb" src={candidate.snapshotUrl} alt="" loading="lazy" />
        <span className="queue__number">#{candidate.candidateNumber}</span>
      </button>
    </li>
  );
}

function SelectedDetail({
  candidate,
  nowMs,
  busy,
  disabled,
  onReview,
  onShowOnMap,
}: {
  candidate: DetectionCandidate;
  nowMs: number;
  busy: boolean;
  disabled: boolean;
  onReview: DetectionsState['review'];
  onShowOnMap: (candidate: DetectionCandidate) => void;
}) {
  const location = candidate.estimatedLocation;
  const isPersonPosition = location !== null && locationIsPersonPosition(location);

  return (
    <div className="detection-detail">
      <img
        className="detection-detail__snapshot"
        src={candidate.snapshotUrl}
        alt={`Snapshot of possible person ${candidate.candidateNumber}`}
      />
      <div className="detection-detail__body">
        <p className="detection-detail__title">Possible person {candidate.candidateNumber}</p>
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
          // Saying nothing would imply the position is merely still loading.
          <p className="detection__meta detection__quality">
            No ground position could be worked out.
          </p>
        )}

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
          <button
            type="button"
            className="btn btn--small"
            // Nothing to centre on, and a fabricated point is worse than none.
            disabled={location === null}
            onClick={() => onShowOnMap(candidate)}
          >
            {location !== null && !isPersonPosition ? 'Show drone position' : 'Show on map'}
          </button>
        </div>

        {location !== null && !isPersonPosition && (
          <p className="detection__meta detection__quality">
            This point is where the drone was when it saw something, not where the person is.
          </p>
        )}
      </div>
    </div>
  );
}

export function DetectionsPanel({ detections, nowMs, onShowOnMap }: Props) {
  const {
    pending,
    selected,
    selectedId,
    loading,
    stale,
    error,
    reviewing,
    reviewError,
    canSelectNeighbour,
  } = detections;
  const problem = reviewError ?? error;

  let content: React.ReactNode;
  if (loading && pending.length === 0) {
    content = <EmptyState>Loading detections…</EmptyState>;
  } else if (error && detections.candidates.length === 0) {
    content = <EmptyState>{detectionErrorMessage(error).title}</EmptyState>;
  } else if (pending.length === 0) {
    // Confirmed candidates may still be held for the map; the queue is still empty.
    content = (
      <EmptyState>
        No pending detections. When the drone spots a possible person, it appears here with a
        snapshot and approximate location for you to confirm or dismiss.
      </EmptyState>
    );
  } else {
    content = (
      <>
        {selected && (
          <SelectedDetail
            candidate={selected}
            nowMs={nowMs}
            busy={reviewing === selected.candidateId}
            // One review at a time: a second decision would race the first.
            disabled={reviewing !== null}
            onReview={detections.review}
            onShowOnMap={onShowOnMap}
          />
        )}
        <div className="queue">
          <ul className="queue__list">
            {pending.map((candidate) => (
              <QueueCard
                key={candidate.candidateId}
                candidate={candidate}
                selected={candidate.candidateId === selectedId}
                onSelect={() => detections.select(candidate.candidateId)}
              />
            ))}
          </ul>
          <div className="queue__nav">
            <button
              type="button"
              className="btn btn--small"
              disabled={!canSelectNeighbour('previous')}
              onClick={() => detections.selectNeighbour('previous')}
            >
              Earlier
            </button>
            <button
              type="button"
              className="btn btn--small"
              disabled={!canSelectNeighbour('next')}
              onClick={() => detections.selectNeighbour('next')}
            >
              Later
            </button>
          </div>
        </div>
      </>
    );
  }

  return (
    <Panel
      title="Possible detections"
      area="detections"
      actions={
        pending.length > 0 ? (
          <span className="panel__badge">{pending.length} to review</span>
        ) : undefined
      }
    >
      {stale && (
        <p className="detection__stale" role="status">
          Disconnected. Showing the last known list, which may be out of date.
        </p>
      )}
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
