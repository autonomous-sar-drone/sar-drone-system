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

/**
 * Read-only preview. No Confirm or Dismiss here: reviewing from a hover popup is
 * too easy to do by accident, and the review queue is the one place a decision is
 * recorded.
 */
export function createPopupElement(candidate: DetectionCandidate, nowMs: number): HTMLElement {
  const root = document.createElement('div');
  root.className = 'detect-popup';

  const image = document.createElement('img');
  image.className = 'detect-popup__thumb';
  // The backend's own path, used as given. Nothing is constructed from parts here.
  image.src = candidate.snapshotUrl;
  image.alt = `Snapshot of possible person ${candidate.candidateNumber}`;
  image.loading = 'lazy';

  const title = document.createElement('p');
  title.className = 'detect-popup__title';
  title.textContent = `Possible person ${candidate.candidateNumber}`;

  const meta = document.createElement('p');
  meta.className = 'detect-popup__meta';
  meta.textContent = `${confidenceText(candidate.confidence)} · ${relativeTime(candidate.capturedAt, nowMs)}`;

  const quality = document.createElement('p');
  quality.className = 'detect-popup__quality';
  quality.textContent = candidate.estimatedLocation
    ? QUALITY_COPY[candidate.estimatedLocation.quality]
    : 'No ground position could be worked out.';

  // Where it was seen, which is not where the drone is now.
  const historical = document.createElement('p');
  historical.className = 'detect-popup__historical';
  historical.textContent = 'Last seen here, not the drone’s current position.';

  root.append(image, title, meta, quality, historical);
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
        existing.element.className = `detect-marker detect-marker--${model.confirmed ? 'confirmed' : 'pending'}`;
        existing.element.setAttribute(
          'aria-label',
          `${model.confirmed ? 'Confirmed person' : 'Possible person'} ${model.candidateNumber}`,
        );
      }
      existing.model = model;
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
    });
    const marker = new maplibregl.Marker({ element }).setLngLat(model.lngLat).addTo(this.map);

    const listeners: Array<[string, EventListener]> = [];
    const open = () => {
      popup.setDOMContent(this.popupFor(candidate));
      popup.setLngLat(model.lngLat).addTo(this.map);
    };
    const close = () => popup.remove();
    const select = (event: Event) => {
      // Without this a click would fall through to the map and drop a polygon
      // vertex under the marker while the operator is still drawing.
      event.stopPropagation();
      event.preventDefault();
      this.options.onSelect?.(model.candidateId);
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

    return { marker, popup, element, model, listeners };
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
