import {
  CANDIDATE_STATUSES,
  LOCATION_METHODS,
  LOCATION_QUALITIES,
  type CandidateStatus,
  type DetectionCandidate,
  type DetectionCollection,
  type DetectionError,
  type EstimatedPersonLocation,
  type LocationMethod,
  type LocationQuality,
  type PixelBoundingBox,
  type ReviewDecision,
} from '../types/detections';

export type DetectionResult<T> = { ok: true; value: T } | { ok: false; error: DetectionError };

interface Options {
  signal?: AbortSignal;
  /** Injected in tests; defaults to the global fetch. */
  fetchImpl?: typeof fetch;
}

function isObj(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function isIsoString(value: unknown): value is string {
  return typeof value === 'string' && !Number.isNaN(Date.parse(value));
}

function parseBoundingBox(raw: unknown): PixelBoundingBox | null {
  if (!isObj(raw)) return null;
  const fields = ['xPx', 'yPx', 'widthPx', 'heightPx', 'frameWidthPx', 'frameHeightPx'] as const;
  for (const field of fields) {
    if (!isFiniteNumber(raw[field])) return null;
  }
  // A zero-sized frame would make every percentage calculation divide by zero.
  if ((raw.frameWidthPx as number) <= 0 || (raw.frameHeightPx as number) <= 0) return null;
  return {
    xPx: raw.xPx as number,
    yPx: raw.yPx as number,
    widthPx: raw.widthPx as number,
    heightPx: raw.heightPx as number,
    frameWidthPx: raw.frameWidthPx as number,
    frameHeightPx: raw.frameHeightPx as number,
  };
}

function parseLocation(raw: unknown): EstimatedPersonLocation | null {
  if (!isObj(raw)) return null;
  if (!isFiniteNumber(raw.latitudeDeg) || Math.abs(raw.latitudeDeg) > 90) return null;
  if (!isFiniteNumber(raw.longitudeDeg) || Math.abs(raw.longitudeDeg) > 180) return null;
  if (typeof raw.method !== 'string' || !LOCATION_METHODS.includes(raw.method as LocationMethod)) {
    return null;
  }
  if (
    typeof raw.quality !== 'string' ||
    !LOCATION_QUALITIES.includes(raw.quality as LocationQuality)
  ) {
    return null;
  }
  return {
    latitudeDeg: raw.latitudeDeg,
    longitudeDeg: raw.longitudeDeg,
    method: raw.method as LocationMethod,
    quality: raw.quality as LocationQuality,
  };
}

/** Returns null rather than throwing, so one bad candidate cannot blank the panel. */
export function parseCandidate(raw: unknown): DetectionCandidate | null {
  if (!isObj(raw)) return null;
  if (typeof raw.candidateId !== 'string' || raw.candidateId === '') return null;
  if (!isFiniteNumber(raw.candidateNumber)) return null;
  if (
    typeof raw.status !== 'string' ||
    !CANDIDATE_STATUSES.includes(raw.status as CandidateStatus)
  ) {
    return null;
  }
  for (const field of ['firstObservedAt', 'capturedAt', 'publishedAt'] as const) {
    if (!isIsoString(raw[field])) return null;
  }
  if (!isFiniteNumber(raw.confidence) || raw.confidence < 0 || raw.confidence > 1) return null;
  if (typeof raw.snapshotUrl !== 'string' || raw.snapshotUrl === '') return null;

  const boundingBox = parseBoundingBox(raw.boundingBox);
  if (boundingBox === null) return null;

  // Nulls are explicit on this event, but absent is tolerated too.
  const executionId = typeof raw.executionId === 'string' ? raw.executionId : null;
  const reviewedAt = isIsoString(raw.reviewedAt) ? raw.reviewedAt : null;
  const estimatedLocation =
    raw.estimatedLocation === undefined || raw.estimatedLocation === null
      ? null
      : parseLocation(raw.estimatedLocation);

  return {
    candidateId: raw.candidateId,
    candidateNumber: raw.candidateNumber,
    executionId,
    status: raw.status as CandidateStatus,
    firstObservedAt: raw.firstObservedAt as string,
    capturedAt: raw.capturedAt as string,
    publishedAt: raw.publishedAt as string,
    reviewedAt,
    confidence: raw.confidence,
    boundingBox,
    estimatedLocation,
    snapshotUrl: raw.snapshotUrl,
  };
}

export function parseCollection(raw: unknown): DetectionCollection | null {
  if (!isObj(raw)) return null;
  if (typeof raw.sessionId !== 'string') return null;
  if (!isFiniteNumber(raw.revision) || raw.revision < 0) return null;
  if (!Array.isArray(raw.candidates)) return null;

  // A single malformed candidate is dropped rather than failing the whole list;
  // losing one row beats showing the operator an empty panel.
  const candidates: DetectionCandidate[] = [];
  for (const item of raw.candidates) {
    const parsed = parseCandidate(item);
    if (parsed !== null) candidates.push(parsed);
  }
  return { sessionId: raw.sessionId, revision: raw.revision, candidates };
}

export function parseDetectionError(status: number, body: unknown): DetectionError {
  if (isObj(body) && isObj(body.detail)) {
    const detail = body.detail;
    if (typeof detail.code === 'string' && typeof detail.message === 'string') {
      return {
        code: detail.code,
        message: detail.message,
        details: isObj(detail.details) ? detail.details : {},
      };
    }
  }
  return {
    code: 'UNEXPECTED_RESPONSE',
    message: `The backend returned an unexpected ${status} response.`,
    details: {},
  };
}

async function request<T>(
  path: string,
  init: RequestInit,
  parse: (body: unknown) => T | null,
  { signal, fetchImpl = fetch }: Options,
): Promise<DetectionResult<T>> {
  let response: Response;
  try {
    response = await fetchImpl(path, { ...init, signal });
  } catch (error) {
    // An abort is the caller unmounting; it must not be reported as a failure.
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    return {
      ok: false,
      error: { code: 'NETWORK_ERROR', message: 'Could not reach the backend.', details: {} },
    };
  }

  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }

  if (!response.ok) return { ok: false, error: parseDetectionError(response.status, body) };

  const value = parse(body);
  if (value === null) {
    return {
      ok: false,
      error: {
        code: 'UNEXPECTED_RESPONSE',
        message: 'The backend response did not match the detection contract.',
        details: {},
      },
    };
  }
  return { ok: true, value };
}

export function fetchDetections(options: Options = {}): Promise<DetectionResult<DetectionCollection>> {
  return request('/api/detections', { method: 'GET' }, parseCollection, options);
}

export interface ReviewOutcome {
  sessionId: string;
  revision: number;
  candidate: DetectionCandidate;
}

function parseReview(body: unknown): ReviewOutcome | null {
  if (!isObj(body)) return null;
  if (typeof body.sessionId !== 'string') return null;
  if (!isFiniteNumber(body.revision) || body.revision < 0) return null;
  const candidate = parseCandidate(body.candidate);
  if (candidate === null) return null;
  return { sessionId: body.sessionId, revision: body.revision, candidate };
}

export function reviewCandidate(
  candidateId: string,
  decision: ReviewDecision,
  options: Options = {},
): Promise<DetectionResult<ReviewOutcome>> {
  return request(
    `/api/detections/${encodeURIComponent(candidateId)}/review`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ decision }),
    },
    parseReview,
    options,
  );
}

/** Snapshot bytes are served by the backend, not embedded in the candidate. */
export function snapshotSrc(candidate: DetectionCandidate): string {
  return candidate.snapshotUrl;
}
