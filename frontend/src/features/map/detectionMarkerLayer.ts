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
 * Read-only preview. No Confirm or Dismiss here: reviewing from a hover popup is
 * too easy to do by accident, and the review queue is the one place a decision is
 * recorded.
 */
/** Read-only, compact preview. Coordinates refer to the historical person estimate. */
export function createPopupElement(candidate: DetectionCandidate, nowMs: number): HTMLElement {
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
  root.append(image, details);
  return root;
}

interface Options {
  /** Selecting from the map moves the review queue; it never records a decision. */
  onSelect?: (candidateId: string) => void;
  /** Read at hover time so the popup shows a current relative time. */
  now?: () => number;
}

interface Entry {
  marker: maplibregl.Marker;
  popup: maplibregl.Popup;
  element: HTMLElement;
  model: MarkerModel;
  candidate: DetectionCandidate;
  listeners: Array<[string, EventListener]>;
}

/** Keeps live MapLibre markers in step with the candidate collection. */
export class DetectionMarkerLayer {
  private entries = new Map<string, Entry>();
  private readonly map: maplibregl.Map;
  private readonly options: Options;

  constructor(map: maplibregl.Map, options: Options = {}) {
    this.map = map;
    this.options = options;
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
      existing.popup.setDOMContent(this.popupFor(candidate));
    }
  }

  private popupFor(candidate: DetectionCandidate): HTMLElement {
    const now = this.options.now?.() ?? Date.now();
    return createPopupElement(candidate, now);
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
    const entry: Entry = { marker, popup, element, model, candidate, listeners };
    const open = () => {
      popup.setDOMContent(this.popupFor(entry.candidate));
      popup.setLngLat(entry.model.lngLat).addTo(this.map);
    };
    const close = () => popup.remove();
    const select = (event: Event) => {
      // Without this a click would fall through to the map and drop a polygon
      // vertex under the marker while the operator is still drawing.
      event.stopPropagation();
      event.preventDefault();
      this.options.onSelect?.(entry.model.candidateId);
    };

    for (const [type, handler] of [
      ['mouseenter', open],
      ['focus', open],
      ['mouseleave', close],
      ['blur', close],
      ['click', select],
    ] as Array<[string, EventListener]>) {
      element.addEventListener(type, handler);
      listeners.push([type, handler]);
    }

    return entry;
  }

  private removeEntry(entry: Entry): void {
    for (const [type, handler] of entry.listeners) {
      entry.element.removeEventListener(type, handler);
    }
    entry.popup.remove();
    entry.marker.remove();
  }

  destroy(): void {
    for (const entry of this.entries.values()) this.removeEntry(entry);
    this.entries.clear();
  }
}
