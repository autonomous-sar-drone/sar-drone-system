import type { DetectionCandidate } from '../../types/detections';

/**
 * Rules for the pending review queue and which card is selected.
 *
 * The authoritative collection in useDetections holds every candidate whatever its
 * status, because the map and the reconnect reconciliation both need the confirmed
 * and dismissed records. The queue here is only a view of that collection: the
 * candidates still waiting on the operator, oldest first, so the row reads left to
 * right in the order the drone found them.
 */

/** Authoritative order: chronological by the backend's own counter. */
export function sortByNumber(candidates: DetectionCandidate[]): DetectionCandidate[] {
  return [...candidates].sort((a, b) => a.candidateNumber - b.candidateNumber);
}

/** The review queue: PENDING only, oldest first. */
export function pendingQueue(candidates: DetectionCandidate[]): DetectionCandidate[] {
  return sortByNumber(candidates.filter((c) => c.status === 'PENDING'));
}

/**
 * Keeps a selection pointing at something real as the queue changes.
 *
 * A candidate still in the queue stays selected, which is what stops a new arrival
 * from stealing focus mid-review. Otherwise the selection falls back to the oldest
 * waiting candidate, which is also the default before the operator has chosen.
 */
export function resolveSelection(
  pending: DetectionCandidate[],
  desiredId: string | null,
): string | null {
  if (desiredId !== null && pending.some((c) => c.candidateId === desiredId)) return desiredId;
  return pending[0]?.candidateId ?? null;
}

export type Direction = 'next' | 'previous';

/**
 * Neighbour in the queue, without wrapping. Returning null at either end is
 * deliberate: wrapping from newest back to oldest would silently re-show a card the
 * operator has already passed over, and they would have no way to tell.
 */
export function neighbourId(
  pending: DetectionCandidate[],
  currentId: string | null,
  direction: Direction,
): string | null {
  if (currentId === null) return pending[0]?.candidateId ?? null;
  const index = pending.findIndex((c) => c.candidateId === currentId);
  if (index === -1) return pending[0]?.candidateId ?? null;
  const target = direction === 'next' ? index + 1 : index - 1;
  if (target < 0 || target >= pending.length) return null;
  return pending[target].candidateId;
}

/**
 * Where selection lands once a reviewed candidate leaves the queue.
 *
 * Next to the right, so the operator keeps moving forward through the backlog, and
 * the nearest remaining card to the left when they have just reviewed the newest.
 * `pendingBefore` is the queue as it was at the moment of review, so this does not
 * depend on when the socket update or the HTTP response happens to land.
 */
export function selectionAfterReview(
  pendingBefore: DetectionCandidate[],
  reviewedId: string,
): string | null {
  const index = pendingBefore.findIndex((c) => c.candidateId === reviewedId);
  if (index === -1) return pendingBefore[0]?.candidateId ?? null;

  const remaining = pendingBefore.filter((c) => c.candidateId !== reviewedId);
  if (remaining.length === 0) return null;
  // After removal the old index now holds what used to be to the right of it.
  if (index < remaining.length) return remaining[index].candidateId;
  return remaining[remaining.length - 1].candidateId;
}
