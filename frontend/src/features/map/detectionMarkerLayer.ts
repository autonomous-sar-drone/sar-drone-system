import * as maplibregl from 'maplibre-gl';
import type { DetectionCandidate } from '../../types/detections';
import { QUALITY_COPY, confidenceText, relativeTime } from '../detections/detectionCopy';
import { isMappable } from './detectionMarkers';

/**
 * Detection markers as DOM overlays rather than circle layers.
 *
 * Two reasons for DOM over a symbol layer: the markers must carry a visible
 * candidate number, and the basemap style ships no glyph source, so a symbol layer
 * could not draw text. DOM also gives each marker a real hover target for the
 * preview without hit-testing the canvas.
 *
 * Shape, not just colour, separates the states: a pending candidate is a numbered
 * triangle and a confirmed one is a filled person mark. Colour alone would be
 * invisible to a colour-blind operator and illegible against a varied basemap.
 */

export interface MarkerModel {
  candidateId: string;
  candidateNumber: number;
  confirmed: boolean;
  /** MapLibre order: longitude first. */
  lngLat: [number, number];
}

/**
 * Which candidates get a marker, and what each one is.
 *
 * Dismissed candidates and camera-position fallbacks are already excluded by
 * isMappable; see detectionMarkers.ts for why the fallback must never appear as a
 * person's location.
 */
export function markerModels(candidates: DetectionCandidate[]): MarkerModel[] {
  const models: MarkerModel[] = [];
  for (const candidate of candidates) {
    if (!isMappable(candidate)) continue;
    const location = candidate.estimatedLocation;
    if (location === null) continue; // narrowed by isMappable; kept for the type checker
    models.push({
      candidateId: candidate.candidateId,
      candidateNumber: candidate.candidateNumber,
      confirmed: candidate.status === 'CONFIRMED',
      lngLat: [location.longitudeDeg, location.latitudeDeg],
    });
  }
  return models;
}

const PENDING_SVG =
  '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">' +
  '<path d="M12 3 L22 21 L2 21 Z" /></svg>';
const CONFIRMED_SVG =
  '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">' +
  '<circle cx="12" cy="7" r="3.4" />' +
  '<path d="M5 21c0-4 3.1-6.6 7-6.6s7 2.6 7 6.6z" /></svg>';

/** The marker element. Only the number is variable, and it is set as text. */
export function createMarkerElement(model: MarkerModel): HTMLElement {
  const el = document.createElement('button');
  el.type = 'button';
  el.className = `detect-marker detect-marker--${model.confirmed ? 'confirmed' : 'pending'}`;
  el.dataset.candidateId = model.candidateId;
  el.setAttribute(
    'aria-label',
    `${model.confirmed ? 'Confirmed person' : 'Possible person'} ${model.candidateNumber}`,
  );

  const shape = document.createElement('span');
  shape.className = 'detect-marker__shape';
  // Fixed literals, never candidate data.
  shape.innerHTML = model.confirmed ? CONFIRMED_SVG : PENDING_SVG;

  const number = document.createElement('span');
  number.className = 'detect-marker__number';
  // textContent, so a candidate number can never be read as markup.
  number.textContent = String(model.candidateNumber);

  el.append(shape, number);
  return el;
}

/** Reapply the actual SVG and label when a candidate is confirmed in place. */
export function updateMarkerElement(el: HTMLElement, model: MarkerModel): void {
  el.className = `detect-marker detect-marker--${model.confirmed ? 'confirmed' : 'pending'}`;
  el.setAttribute(
    'aria-label',
    `${model.confirmed ? 'Confirmed person' : 'Possible person'} ${model.candidateNumber}`,
  );
  const shape = el.querySelector('.detect-marker__shape');
  if (shape) shape.innerHTML = model.confirmed ? CONFIRMED_SVG : PENDING_SVG;
  const number = el.querySelector('.detect-marker__number');
  if (number) number.textContent = String(model.candidateNumber);
}

/**
 * PHASE 2. Whether this sighting can be revisited right now, and why not if it
 * cannot. Two independent gates, and the backend owns both of them.
 */
export interface RevisitPolicy {
  /** Mission side: the aircraft is in a state that accepts a revisit. */
  missionAllows: boolean;
  /** Shown when the mission side is what blocks it. */
  missionReason: string | null;
}

export const REVISIT_CLOSED: RevisitPolicy = {
  missionAllows: false,
  missionReason: 'Not available right now.',
};

export interface RevisitEligibility {
  /** Whether to render the control at all. Dismissed sightings get nothing. */
  offered: boolean;
  enabled: boolean;
  reason: string | null;
}

/**
 * Revisit is offered for pending and confirmed sightings only.
 *
 * A dismissed sighting is one the operator has already ruled out, and it carries no
 * marker either, so offering to fly back to it would be reviving a decision they
 * made. Everything else is the backend's call: revisitAvailable means it holds a
 * usable capture pose, and missionAllows means the aircraft can act on it.
 */
export function revisitEligibility(
  candidate: DetectionCandidate,
  policy: RevisitPolicy,
): RevisitEligibility {
  if (candidate.status === 'DISMISSED') {
    return { offered: false, enabled: false, reason: null };
  }
  if (!candidate.revisitAvailable) {
    return {
      offered: true,
      enabled: false,
      reason: candidate.revisitUnavailableReason ?? 'No stored viewpoint for this sighting.',
    };
  }
  if (!policy.missionAllows) {
    return { offered: true, enabled: false, reason: policy.missionReason ?? 'Not available right now.' };
  }
  return { offered: true, enabled: true, reason: null };
}

export interface PopupActions {
  policy: RevisitPolicy;
  /** Called with the candidate id only. Never with a coordinate. */
  onRevisit?: (candidateId: string) => void;
}

/**
 * Read-only preview plus, in Phase 2, a single navigation action. Still no Confirm
 * or Dismiss here: reviewing from a hover popup is too easy to do by accident, and
 * the review queue is the one place a decision is recorded.
 *
 * Coordinates shown refer to the historical person estimate. The Revisit action does
 * NOT use them: it sends the candidate id and lets the backend fly to the stored
 * camera pose.
 */
export function createPopupElement(
  candidate: DetectionCandidate,
  nowMs: number,
  actions?: PopupActions,
): HTMLElement {
  const root = document.createElement('div');
  root.className = 'detect-popup';

  const image = document.createElement('img');
  image.className = 'detect-popup__thumb';
  image.src = candidate.snapshotUrl;
  image.alt = `Annotated snapshot of possible person ${candidate.candidateNumber}`;
  image.loading = 'lazy';

  const details = document.createElement('div');
  details.className = 'detect-popup__details';

  const heading = document.createElement('div');
  heading.className = 'detect-popup__heading';
  const title = document.createElement('p');
  title.className = 'detect-popup__title';
  title.textContent = `Possible person #${candidate.candidateNumber}`;
  heading.appendChild(title);

  if (candidate.status === 'CONFIRMED') {
    const state = document.createElement('span');
    state.className = 'detect-popup__state';
    state.textContent = 'Confirmed';
    heading.appendChild(state);
  }

  const meta = document.createElement('p');
  meta.className = 'detect-popup__meta';
  const confidence = document.createElement('span');
  confidence.textContent = confidenceText(candidate.confidence);
  const captured = document.createElement('time');
  captured.dateTime = candidate.capturedAt;
  captured.title = `${candidate.capturedAt} · ${relativeTime(candidate.capturedAt, nowMs)}`;
  const capturedMs = Date.parse(candidate.capturedAt);
  captured.textContent = Number.isNaN(capturedMs)
    ? 'Time unavailable'
    : new Intl.DateTimeFormat(undefined, {
        month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
      }).format(new Date(capturedMs));
  meta.append(confidence, captured);

  const location = candidate.estimatedLocation;
  const coordinates = document.createElement('p');
  coordinates.className = 'detect-popup__coordinates';
  const quality = document.createElement('p');
  quality.className = 'detect-popup__quality';
  if (location === null) {
    coordinates.textContent = 'Location unavailable';
    quality.textContent = 'No ground position could be worked out.';
  } else {
    // Human-readable hemispheres; the backend's coordinates are authoritative.
    const lat = `${Math.abs(location.latitudeDeg).toFixed(5)}° ${location.latitudeDeg >= 0 ? 'N' : 'S'}`;
    const lon = `${Math.abs(location.longitudeDeg).toFixed(5)}° ${location.longitudeDeg >= 0 ? 'E' : 'W'}`;
    coordinates.textContent = `${lat}, ${lon}`;
    quality.textContent = location.quality === 'APPROXIMATE'
      ? 'Estimated (flat ground) · Last seen here'
      : location.quality === 'TERRAIN_ADJUSTED'
        ? 'Estimated (terrain-adjusted) · Last seen here'
        : QUALITY_COPY[location.quality]; // Never call a drone-position fallback a person estimate.
  }

  details.append(heading, meta, coordinates, quality);

  // PHASE 2. Appended last so the snapshot, number, confidence, time and estimated
  // position above are untouched by it.
  if (actions !== undefined) {
    const eligibility = revisitEligibility(candidate, actions.policy);
    if (eligibility.offered) {
      const row = document.createElement('div');
      row.className = 'detect-popup__actions';

      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'btn btn--small';
      button.dataset.revisit = candidate.candidateId;
      button.textContent = 'Revisit viewpoint';
      button.disabled = !eligibility.enabled;
      if (eligibility.reason !== null) {
        button.title = eligibility.reason;
        button.setAttribute('aria-description', eligibility.reason);
      }
      button.addEventListener('click', (event) => {
        // The map is underneath. Without this, a click here would also drop a
        // polygon vertex, select the marker, or start a drag.
        event.stopPropagation();
        event.preventDefault();
        if (!eligibility.enabled) return;
        // Id only. The destination is the backend's stored capture pose, not the
        // estimated person location this popup is displaying.
        actions.onRevisit?.(candidate.candidateId);
      });
      row.appendChild(button);

      if (eligibility.reason !== null) {
        const reason = document.createElement('p');
        reason.className = 'detect-popup__reason';
        reason.textContent = eligibility.reason;
        row.appendChild(reason);
      }
      details.appendChild(row);
    }
  }

  root.append(image, details);
  return root;
}

interface Options {
  /** Selecting from the map moves the review queue; it never records a decision. */
  onSelect?: (candidateId: string) => void;
  /** PHASE 2. Receives a candidate id only; never a coordinate. */
  onRevisit?: (candidateId: string) => void;
  /** Read at hover time so the popup shows a current relative time. */
  now?: () => number;
  /**
   * How long the popup survives after the pointer leaves both the marker and the
   * popup. There is a real gap between them, so closing on the first mouseleave
   * would make the Revisit button unreachable with a mouse.
   */
  graceMs?: number;
}

interface Entry {
  marker: maplibregl.Marker;
  popup: maplibregl.Popup;
  element: HTMLElement;
  model: MarkerModel;
  candidate: DetectionCandidate;
  listeners: Array<[string, EventListener]>;
  /** Latest popup content, so listeners and focus moves can find it. */
  content: HTMLElement | null;
  pointerOverMarker: boolean;
  pointerOverPopup: boolean;
  focusWithin: boolean;
  closeTimer: ReturnType<typeof setTimeout> | null;
}

const DEFAULT_GRACE_MS = 240;

/** Keeps live MapLibre markers in step with the candidate collection. */
export class DetectionMarkerLayer {
  private entries = new Map<string, Entry>();
  private readonly map: maplibregl.Map;
  private readonly options: Options;
  private readonly graceMs: number;
  private policy: RevisitPolicy = REVISIT_CLOSED;

  constructor(map: maplibregl.Map, options: Options = {}) {
    this.map = map;
    this.options = options;
    this.graceMs = options.graceMs ?? DEFAULT_GRACE_MS;
  }

  /**
   * PHASE 2. Mission-side revisit permission, which changes independently of the
   * candidates. Any popup already on screen is re-rendered so an action cannot stay
   * clickable after the aircraft stopped being able to accept it.
   */
  setRevisitPolicy(policy: RevisitPolicy): void {
    if (
      this.policy.missionAllows === policy.missionAllows &&
      this.policy.missionReason === policy.missionReason
    ) {
      return;
    }
    this.policy = policy;
    for (const entry of this.entries.values()) {
      if (entry.content !== null) this.renderPopup(entry);
    }
  }

  sync(candidates: DetectionCandidate[]): void {
    const byId = new Map(candidates.map((c) => [c.candidateId, c]));
    const models = markerModels(candidates);
    const wanted = new Set(models.map((m) => m.candidateId));

    // A candidate that is gone, dismissed, or from a dead session loses its marker
    // along with its listeners and popup.
    for (const [id, entry] of this.entries) {
      if (!wanted.has(id)) {
        this.removeEntry(entry);
        this.entries.delete(id);
      }
    }

    for (const model of models) {
      const candidate = byId.get(model.candidateId);
      if (candidate === undefined) continue;
      const existing = this.entries.get(model.candidateId);

      if (existing === undefined) {
        this.entries.set(model.candidateId, this.addEntry(model, candidate));
        continue;
      }

      // Identity and position must not move across a review, so only the state
      // that changes the look is reapplied.
      if (existing.model.confirmed !== model.confirmed) {
        updateMarkerElement(existing.element, model);
      }
      existing.model = model;
      existing.candidate = candidate;
      if (existing.content !== null) this.renderPopup(existing);
    }
  }

  /** Build the popup content, wire its listeners, and hand it to MapLibre. */
  private renderPopup(entry: Entry): void {
    const now = this.options.now?.() ?? Date.now();
    const content = createPopupElement(entry.candidate, now, {
      policy: this.policy,
      onRevisit: (candidateId) => this.options.onRevisit?.(candidateId),
    });

    // The popup floats above the map canvas, so anything that starts here must be
    // stopped here or the map will also act on it.
    content.addEventListener('mousedown', (event) => event.stopPropagation());
    content.addEventListener('click', (event) => event.stopPropagation());

    content.addEventListener('mouseenter', () => {
      entry.pointerOverPopup = true;
      this.cancelClose(entry);
    });
    content.addEventListener('mouseleave', () => {
      entry.pointerOverPopup = false;
      this.scheduleClose(entry);
    });
    content.addEventListener('focusin', () => {
      entry.focusWithin = true;
      this.cancelClose(entry);
    });
    content.addEventListener('focusout', (event) => {
      const next = (event as FocusEvent).relatedTarget;
      // Moving between controls inside the popup, or back onto the marker, is not
      // leaving: only a destination outside both counts.
      if (next instanceof Node && (content.contains(next) || entry.element.contains(next))) return;
      entry.focusWithin = false;
      this.scheduleClose(entry);
    });
    content.addEventListener('keydown', (event) => {
      if ((event as KeyboardEvent).key !== 'Escape') return;
      event.stopPropagation();
      // Focus goes back to the marker rather than nowhere, so Escape does not strand
      // a keyboard operator at the top of the document.
      entry.focusWithin = false;
      entry.element.focus();
      this.closeNow(entry);
    });

    entry.content = content;
    entry.popup.setDOMContent(content);
  }

  private openPopup(entry: Entry): void {
    this.cancelClose(entry);
    this.renderPopup(entry);
    entry.popup.setLngLat(entry.model.lngLat).addTo(this.map);
  }

  private scheduleClose(entry: Entry): void {
    this.cancelClose(entry);
    entry.closeTimer = setTimeout(() => {
      entry.closeTimer = null;
      if (entry.pointerOverMarker || entry.pointerOverPopup || entry.focusWithin) return;
      this.closeNow(entry);
    }, this.graceMs);
  }

  private cancelClose(entry: Entry): void {
    if (entry.closeTimer !== null) clearTimeout(entry.closeTimer);
    entry.closeTimer = null;
  }

  private closeNow(entry: Entry): void {
    this.cancelClose(entry);
    entry.content = null;
    entry.popup.remove();
  }

  private addEntry(model: MarkerModel, candidate: DetectionCandidate): Entry {
    const element = createMarkerElement(model);
    const popup = new maplibregl.Popup({
      closeButton: false,
      closeOnClick: false,
      offset: 14,
      maxWidth: '520px',
      className: 'sar-detection-popup',
    });
    const marker = new maplibregl.Marker({ element }).setLngLat(model.lngLat).addTo(this.map);

    const listeners: Array<[string, EventListener]> = [];
    const entry: Entry = {
      marker,
      popup,
      element,
      model,
      candidate,
      listeners,
      content: null,
      pointerOverMarker: false,
      pointerOverPopup: false,
      focusWithin: false,
      closeTimer: null,
    };

    const pointerEnter = () => {
      entry.pointerOverMarker = true;
      this.openPopup(entry);
    };
    const pointerLeave = () => {
      entry.pointerOverMarker = false;
      // Grace period, not an immediate close: the pointer has to cross a gap to
      // reach the popup, and closing on the way would make Revisit unclickable.
      this.scheduleClose(entry);
    };
    const gainFocus = () => {
      entry.focusWithin = true;
      this.openPopup(entry);
    };
    const loseFocus = (event: Event) => {
      const next = (event as FocusEvent).relatedTarget;
      if (next instanceof Node && entry.content?.contains(next)) return;
      entry.focusWithin = false;
      this.scheduleClose(entry);
    };
    const select = (event: Event) => {
      // Without this a click would fall through to the map and drop a polygon
      // vertex under the marker while the operator is still drawing.
      event.stopPropagation();
      event.preventDefault();
      this.options.onSelect?.(entry.model.candidateId);
    };
    const keys = (event: Event) => {
      const key = (event as KeyboardEvent).key;
      if (key === 'Escape') {
        event.stopPropagation();
        this.closeNow(entry);
        return;
      }
      // The popup is a sibling of the marker in the DOM, not a child, so natural Tab
      // order does not reach it. ArrowDown and Tab both step into it, which is what
      // makes Revisit usable without a mouse.
      if (key !== 'ArrowDown' && key !== 'Tab') return;
      if ((event as KeyboardEvent).shiftKey) return;
      const action = entry.content?.querySelector<HTMLButtonElement>('[data-revisit]');
      if (!action || action.disabled) return;
      event.preventDefault();
      event.stopPropagation();
      entry.focusWithin = true;
      this.cancelClose(entry);
      action.focus();
    };

    for (const [type, handler] of [
      ['mouseenter', pointerEnter],
      ['mouseleave', pointerLeave],
      ['focus', gainFocus],
      ['blur', loseFocus],
      ['click', select],
      ['keydown', keys],
    ] as Array<[string, EventListener]>) {
      element.addEventListener(type, handler);
      listeners.push([type, handler]);
    }

    return entry;
  }

  private removeEntry(entry: Entry): void {
    // Timer first: a pending close firing against a removed popup is harmless but a
    // leaked interval across a map teardown is not.
    this.cancelClose(entry);
    for (const [type, handler] of entry.listeners) {
      entry.element.removeEventListener(type, handler);
    }
    entry.content = null;
    entry.popup.remove();
    entry.marker.remove();
  }

  destroy(): void {
    for (const entry of this.entries.values()) this.removeEntry(entry);
    this.entries.clear();
  }
}
