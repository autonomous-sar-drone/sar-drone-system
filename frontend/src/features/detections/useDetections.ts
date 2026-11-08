import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { fetchDetections, reviewCandidate } from '../../api/detectionsClient';
import type {
  DetectionCandidate,
  DetectionError,
  DetectionUpdateData,
  ReviewDecision,
} from '../../types/detections';
import {
  neighbourId,
  pendingQueue,
  resolveSelection,
  selectionAfterReview,
  sortByNumber,
  type Direction,
} from './reviewQueue';

export interface DetectionsState {
  /**
   * Every candidate the backend has reported, whatever its status. The map and the
   * reconnect reconciliation both read this, so confirmed and dismissed records stay
   * here after they leave the review queue.
   */
  candidates: DetectionCandidate[];
  /** The review queue: PENDING only, oldest first. */
  pending: DetectionCandidate[];
  selectedId: string | null;
  selected: DetectionCandidate | null;
  loading: boolean;
  /**
   * True when the socket is down, so the collection may have moved on without us.
   * The panel says so rather than implying what it shows is current.
   */
  stale: boolean;
  error: DetectionError | null;
  reviewing: string | null;
  reviewError: DetectionError | null;
  select: (candidateId: string) => void;
  /** Move through the queue without recording any decision. */
  selectNeighbour: (direction: Direction) => void;
  canSelectNeighbour: (direction: Direction) => boolean;
  review: (candidateId: string, decision: ReviewDecision) => Promise<void>;
  dismissReviewError: () => void;
}

function mergeCandidate(
  current: DetectionCandidate[],
  incoming: DetectionCandidate,
): DetectionCandidate[] {
  const index = current.findIndex((item) => item.candidateId === incoming.candidateId);
  if (index === -1) return sortByNumber([...current, incoming]);
  const next = [...current];
  next[index] = incoming;
  return sortByNumber(next);
}

interface Options {
  /**
   * Increments every time the events socket re-establishes. GET /api/detections is
   * authoritative, so each reconnect triggers a reconcile: a candidate reviewed
   * while we were disconnected may never produce a detection_update we can see.
   */
  reconnectGeneration?: number;
  /** False while the socket is down, which makes the held collection suspect. */
  connected?: boolean;
}

/**
 * Holds the candidate collection and the operator's place in the review queue.
 *
 * The socket only ever streams single candidates, never the whole list, so the
 * collection is fetched over HTTP and then kept current from the socket. Three
 * things force a refetch: a new backend session, a revision that skips ahead
 * (meaning an update was missed), and every socket reconnect.
 */
export function useDetections(
  update: DetectionUpdateData | null,
  { reconnectGeneration = 0, connected = true }: Options = {},
): DetectionsState {
  const [candidates, setCandidates] = useState<DetectionCandidate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<DetectionError | null>(null);
  const [reviewing, setReviewing] = useState<string | null>(null);
  const [reviewError, setReviewError] = useState<DetectionError | null>(null);
  const [desiredId, setDesiredId] = useState<string | null>(null);

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
    // Only the newest load may write. A slow earlier response landing late would
    // otherwise overwrite fresher state with what the backend said a moment ago.
    const token = ++reloadToken.current;

    fetchDetections({ signal: controller.signal })
      .then((result) => {
        if (!mounted.current || token !== reloadToken.current) return;
        if (result.ok) {
          sessionId.current = result.value.sessionId;
          revision.current = result.value.revision;
          setCandidates(sortByNumber(result.value.candidates));
          setError(null);
        } else {
          setError(result.error);
        }
        setLoading(false);
      })
      .catch(() => {
        // Aborted on unmount or superseded by a newer reload; nothing to report.
      });

    return () => controller.abort();
    // reconnectGeneration is a reload trigger: one refetch per socket re-establishment,
    // which is what keeps this from becoming a GET storm while the socket flaps.
  }, [reloadCount, reconnectGeneration]);

  useEffect(() => {
    if (update === null) return;

    // A different session means the backend restarted and our ids are stale.
    if (sessionId.current !== null && update.sessionId !== sessionId.current) {
      sessionId.current = update.sessionId;
      revision.current = -1;
      reload();
      return;
    }

    // Revisions are consecutive within a session. A jump means at least one update
    // never arrived, so the list is incomplete and only a refetch makes it whole.
    if (revision.current >= 0 && update.revision > revision.current + 1) {
      reload();
      return;
    }

    // An out-of-order or repeated update must not move a row backwards.
    if (update.revision <= revision.current) return;

    revision.current = update.revision;
    setCandidates((current) => mergeCandidate(current, update.candidate));
  }, [update, reload]);

  const pending = useMemo(() => pendingQueue(candidates), [candidates]);
  const selectedId = useMemo(() => resolveSelection(pending, desiredId), [pending, desiredId]);
  const selected = useMemo(
    () => pending.find((c) => c.candidateId === selectedId) ?? null,
    [pending, selectedId],
  );

  const review = useCallback(
    async (candidateId: string, decision: ReviewDecision) => {
      setReviewing(candidateId);
      setReviewError(null);
      // Captured before the queue changes, so where selection lands does not depend
      // on whether the socket update or the HTTP response arrives first.
      const queueAtReview = pending;
      try {
        const result = await reviewCandidate(candidateId, decision);
        if (!mounted.current) return;
        if (result.ok) {
          // The matching detection_update may arrive first, or not at all if the
          // socket is down, so the response is applied here too. Both carry the
          // same candidate, so applying twice is harmless.
          setCandidates((current) => mergeCandidate(current, result.value.candidate));
          if (candidateId === selectedId) {
            setDesiredId(selectionAfterReview(queueAtReview, candidateId));
          }
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
    },
    [pending, selectedId, reload],
  );

  const selectNeighbour = useCallback(
    (direction: Direction) => {
      const target = neighbourId(pending, selectedId, direction);
      // Null means an end of the queue; staying put beats wrapping silently.
      if (target !== null) setDesiredId(target);
    },
    [pending, selectedId],
  );

  const canSelectNeighbour = useCallback(
    (direction: Direction) => neighbourId(pending, selectedId, direction) !== null,
    [pending, selectedId],
  );

  return {
    candidates,
    pending,
    selectedId,
    selected,
    loading,
    stale: !connected,
    error,
    reviewing,
    reviewError,
    select: useCallback((candidateId: string) => setDesiredId(candidateId), []),
    selectNeighbour,
    canSelectNeighbour,
    review,
    dismissReviewError: useCallback(() => setReviewError(null), []),
  };
}
