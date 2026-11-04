import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchDetections, reviewCandidate } from '../../api/detectionsClient';
import type {
  DetectionCandidate,
  DetectionError,
  DetectionUpdateData,
  ReviewDecision,
} from '../../types/detections';

export interface DetectionsState {
  candidates: DetectionCandidate[];
  loading: boolean;
  /** Failure loading the collection, as opposed to a failed review. */
  error: DetectionError | null;
  /** Candidate id currently being reviewed, so one row can show a busy state. */
  reviewing: string | null;
  reviewError: DetectionError | null;
  review: (candidateId: string, decision: ReviewDecision) => Promise<void>;
  dismissReviewError: () => void;
}

/**
 * Unreviewed candidates come first because they are the ones needing the operator,
 * and within each group the newest is on top.
 */
function sortForOperator(candidates: DetectionCandidate[]): DetectionCandidate[] {
  return [...candidates].sort((a, b) => {
    const aPending = a.status === 'PENDING' ? 0 : 1;
    const bPending = b.status === 'PENDING' ? 0 : 1;
    if (aPending !== bPending) return aPending - bPending;
    return b.candidateNumber - a.candidateNumber;
  });
}

function mergeCandidate(
  current: DetectionCandidate[],
  incoming: DetectionCandidate,
): DetectionCandidate[] {
  const index = current.findIndex((item) => item.candidateId === incoming.candidateId);
  if (index === -1) return sortForOperator([...current, incoming]);
  const next = [...current];
  next[index] = incoming;
  return sortForOperator(next);
}

/**
 * Holds the candidate collection.
 *
 * The socket only ever streams single candidates, never the whole list, so the
 * collection is fetched once over HTTP and then kept current from the socket.
 * Two things force a refetch: a new backend session id, and a revision that skips
 * ahead, which means an update was missed and the local list is now incomplete.
 */
export function useDetections(update: DetectionUpdateData | null): DetectionsState {
  const [candidates, setCandidates] = useState<DetectionCandidate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<DetectionError | null>(null);
  const [reviewing, setReviewing] = useState<string | null>(null);
  const [reviewError, setReviewError] = useState<DetectionError | null>(null);

  const sessionId = useRef<string | null>(null);
  const revision = useRef<number>(-1);
  const mounted = useRef(true);
  const reloadToken = useRef(0);

  const [reloadCount, setReloadCount] = useState(0);
  const reload = useCallback(() => {
    setLoading(true);
    setReloadCount((n) => n + 1);
  }, []);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const token = ++reloadToken.current;
    // loading starts true and is set by reload(), so there is nothing to set here.

    fetchDetections({ signal: controller.signal })
      .then((result) => {
        // A slower earlier load must not overwrite a newer one.
        if (!mounted.current || token !== reloadToken.current) return;
        if (result.ok) {
          sessionId.current = result.value.sessionId;
          revision.current = result.value.revision;
          setCandidates(sortForOperator(result.value.candidates));
          setError(null);
        } else {
          setError(result.error);
        }
        setLoading(false);
      })
      .catch(() => {
        // Aborted on unmount or superseded by a reload; nothing to report.
      });

    return () => controller.abort();
  }, [reloadCount]);

  useEffect(() => {
    if (update === null) return;

    // A different session means the backend restarted and the ids we hold are stale.
    if (sessionId.current !== null && update.sessionId !== sessionId.current) {
      sessionId.current = update.sessionId;
      revision.current = -1;
      reload();
      return;
    }

    // Revisions are consecutive. A jump means at least one update never arrived,
    // so the list is incomplete and only a refetch can make it whole again.
    if (revision.current >= 0 && update.revision > revision.current + 1) {
      reload();
      return;
    }

    // An out-of-order or repeated update must not move a row backwards.
    if (update.revision <= revision.current) return;

    revision.current = update.revision;
    setCandidates((current) => mergeCandidate(current, update.candidate));
  }, [update, reload]);

  const review = useCallback(async (candidateId: string, decision: ReviewDecision) => {
    setReviewing(candidateId);
    setReviewError(null);
    try {
      const result = await reviewCandidate(candidateId, decision);
      if (!mounted.current) return;
      if (result.ok) {
        // The matching detection_update may arrive first or not at all if the
        // socket is down, so the response is applied here too. Both carry the
        // same candidate, so applying twice is harmless.
        setCandidates((current) => mergeCandidate(current, result.value.candidate));
      } else {
        setReviewError(result.error);
        // Someone else already reviewed it, so our copy of that row is wrong.
        if (result.error.code === 'CANDIDATE_ALREADY_REVIEWED') reload();
      }
    } catch {
      // Aborted; nothing to update.
    } finally {
      if (mounted.current) setReviewing(null);
    }
  }, [reload]);

  return {
    candidates,
    loading,
    error,
    reviewing,
    reviewError,
    review,
    dismissReviewError: useCallback(() => setReviewError(null), []),
  };
}
