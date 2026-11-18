import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CandidateStatus, DetectionCandidate } from '../../types/detections';

/**
 * MapLibre needs WebGL, which jsdom has none of, so Marker and Popup are replaced
 * with the smallest things that put real elements in the document. Everything the
 * layer does that matters here is DOM work.
 */
vi.mock('maplibre-gl', () => {
  class Popup {
    content: HTMLElement | null = null;
    container = document.createElement('div');
    constructor() {
      this.container.className = 'popup-container';
    }
    setDOMContent(el: HTMLElement) {
      this.content = el;
      this.container.replaceChildren(el);
      return this;
    }
    setLngLat() {
      return this;
    }
    addTo() {
      document.body.appendChild(this.container);
      return this;
    }
    remove() {
      this.container.remove();
      return this;
    }
  }
  class Marker {
    element: HTMLElement;
    constructor(options: { element: HTMLElement }) {
      this.element = options.element;
    }
    setLngLat() {
      return this;
    }
    addTo() {
      document.body.appendChild(this.element);
      return this;
    }
    remove() {
      this.element.remove();
      return this;
    }
  }
  return { Popup, Marker, Map: class {} };
});

const { DetectionMarkerLayer, createPopupElement, revisitEligibility, REVISIT_CLOSED } =
  await import('./detectionMarkerLayer');

function candidate(
  candidateNumber = 1,
  extra: Partial<DetectionCandidate> = {},
): DetectionCandidate {
  return {
    candidateId: `cand-${candidateNumber}`,
    candidateNumber,
    executionId: 'exec-1',
    status: 'PENDING' as CandidateStatus,
    firstObservedAt: '2026-11-17T15:00:00.000Z',
    capturedAt: '2026-11-17T15:00:00.000Z',
    publishedAt: '2026-11-17T15:00:00.000Z',
    reviewedAt: null,
    confidence: 0.82,
    boundingBox: {
      xPx: 1, yPx: 1, widthPx: 10, heightPx: 10, frameWidthPx: 640, frameHeightPx: 480,
    },
    estimatedLocation: {
      latitudeDeg: 30.2291,
      longitudeDeg: -97.7555,
      method: 'TERRAIN_RAYCAST',
      quality: 'TERRAIN_ADJUSTED',
    },
    snapshotUrl: `/api/detections/cand-${candidateNumber}/snapshot`,
    revisitAvailable: true,
    revisitUnavailableReason: null,
    ...extra,
  };
}

const OPEN = { missionAllows: true, missionReason: null };
const NOW = Date.parse('2026-11-17T15:02:00.000Z');
const GRACE = 240;

beforeEach(() => {
  document.body.replaceChildren();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('revisitEligibility', () => {
  it('offers a revisit for pending and confirmed sightings', () => {
    expect(revisitEligibility(candidate(1), OPEN)).toEqual({
      offered: true, enabled: true, reason: null,
    });
    expect(revisitEligibility(candidate(2, { status: 'CONFIRMED' }), OPEN).enabled).toBe(true);
  });

  it('offers nothing for a dismissed sighting', () => {
    // The operator already ruled it out. Offering to fly back would be reviving a
    // decision they made.
    expect(revisitEligibility(candidate(3, { status: 'DISMISSED' }), OPEN).offered).toBe(false);
  });

  it('disables with the backend reason when there is no stored viewpoint', () => {
    const blocked = candidate(4, {
      revisitAvailable: false,
      revisitUnavailableReason: 'Capture pose was not recorded.',
    });
    expect(revisitEligibility(blocked, OPEN)).toEqual({
      offered: true, enabled: false, reason: 'Capture pose was not recorded.',
    });
  });

  it('falls back to its own wording when the backend gives no reason', () => {
    const blocked = candidate(5, { revisitAvailable: false });
    expect(revisitEligibility(blocked, OPEN).reason).toBe('No stored viewpoint for this sighting.');
  });

  it('disables when the aircraft cannot accept a revisit', () => {
    const result = revisitEligibility(candidate(6), {
      missionAllows: false,
      missionReason: 'Already at a viewpoint.',
    });
    expect(result).toEqual({ offered: true, enabled: false, reason: 'Already at a viewpoint.' });
  });

  it('is closed by default', () => {
    expect(revisitEligibility(candidate(7), REVISIT_CLOSED).enabled).toBe(false);
  });
});

describe('popup content', () => {
  it('keeps the snapshot, number, confidence, time and estimated position', () => {
    const root = createPopupElement(candidate(9), NOW, { policy: OPEN });
    expect(root.querySelector('img')?.getAttribute('src')).toBe('/api/detections/cand-9/snapshot');
    expect(root.textContent).toContain('#9');
    expect(root.textContent).toContain('82%');
    expect(root.querySelector('time')).not.toBeNull();
    expect(root.textContent).toContain('30.22910');
  });

  it('adds the revisit action without touching the rest', () => {
    const plain = createPopupElement(candidate(9), NOW);
    const withAction = createPopupElement(candidate(9), NOW, { policy: OPEN });
    expect(plain.querySelector('[data-revisit]')).toBeNull();
    const button = withAction.querySelector<HTMLButtonElement>('[data-revisit]');
    expect(button?.textContent).toBe('Revisit viewpoint');
    expect(button?.disabled).toBe(false);
  });

  it('shows the reason on a disabled action', () => {
    const root = createPopupElement(
      candidate(9, { revisitAvailable: false, revisitUnavailableReason: 'No capture pose.' }),
      NOW,
      { policy: OPEN },
    );
    const button = root.querySelector<HTMLButtonElement>('[data-revisit]');
    expect(button?.disabled).toBe(true);
    expect(button?.title).toBe('No capture pose.');
    expect(root.textContent).toContain('No capture pose.');
  });

  it('passes the candidate id and nothing else', () => {
    // The destination is the backend's stored camera pose. Handing over the person
    // estimate shown right above the button would send a rescuer to the wrong place.
    const onRevisit = vi.fn();
    const subject = candidate(9);
    const root = createPopupElement(subject, NOW, { policy: OPEN, onRevisit });
    root.querySelector<HTMLButtonElement>('[data-revisit]')?.click();

    expect(onRevisit).toHaveBeenCalledTimes(1);
    expect(onRevisit.mock.calls[0]).toEqual(['cand-9']);
    const serialized = JSON.stringify(onRevisit.mock.calls);
    expect(serialized).not.toContain('30.2291');
    expect(serialized).not.toContain('-97.7555');
  });

  it('does nothing when the action is disabled', () => {
    const onRevisit = vi.fn();
    const root = createPopupElement(candidate(9, { revisitAvailable: false }), NOW, {
      policy: OPEN,
      onRevisit,
    });
    root.querySelector<HTMLButtonElement>('[data-revisit]')?.click();
    expect(onRevisit).not.toHaveBeenCalled();
  });
});

describe('popup lifecycle on the map', () => {
  function setup(candidates = [candidate(1)]) {
    const map = {} as never;
    const onRevisit = vi.fn();
    const onSelect = vi.fn();
    const layer = new DetectionMarkerLayer(map, {
      onSelect,
      onRevisit,
      now: () => NOW,
      graceMs: GRACE,
    });
    layer.setRevisitPolicy(OPEN);
    layer.sync(candidates);
    const marker = document.querySelector<HTMLElement>('.detect-marker');
    if (marker === null) throw new Error('marker was not added');
    return { layer, marker, onRevisit, onSelect };
  }

  const popup = () => document.querySelector('.detect-popup');
  const action = () => document.querySelector<HTMLButtonElement>('[data-revisit]');

  it('opens on hover and on focus', () => {
    const { marker } = setup();
    expect(popup()).toBeNull();

    marker.dispatchEvent(new MouseEvent('mouseenter'));
    expect(popup()).not.toBeNull();

    marker.dispatchEvent(new MouseEvent('mouseleave'));
    vi.advanceTimersByTime(GRACE + 10);
    expect(popup()).toBeNull();

    marker.dispatchEvent(new FocusEvent('focus'));
    expect(popup()).not.toBeNull();
  });

  it('ships a non-zero grace period by default', () => {
    // Every other test here sets graceMs, so without this one the default could be
    // zero and the popup would be unreachable with a mouse in the real app.
    const layer = new DetectionMarkerLayer({} as never, { now: () => NOW });
    layer.setRevisitPolicy(OPEN);
    layer.sync([candidate(1)]);
    const marker = document.querySelector<HTMLElement>('.detect-marker');
    marker?.dispatchEvent(new MouseEvent('mouseenter'));
    marker?.dispatchEvent(new MouseEvent('mouseleave'));

    vi.advanceTimersByTime(100);
    expect(popup()).not.toBeNull();
    layer.destroy();
  });

  it('survives the gap between the marker and the popup', () => {
    // There is real empty space between them. Closing on the first mouseleave would
    // make the Revisit button impossible to reach with a mouse.
    const { marker } = setup();
    marker.dispatchEvent(new MouseEvent('mouseenter'));
    marker.dispatchEvent(new MouseEvent('mouseleave'));

    vi.advanceTimersByTime(GRACE - 50);
    expect(popup()).not.toBeNull();

    popup()?.dispatchEvent(new MouseEvent('mouseenter'));
    vi.advanceTimersByTime(1000);
    expect(popup()).not.toBeNull();
    expect(action()).not.toBeNull();
  });

  it('closes once the pointer has left both', () => {
    const { marker } = setup();
    marker.dispatchEvent(new MouseEvent('mouseenter'));
    marker.dispatchEvent(new MouseEvent('mouseleave'));
    popup()?.dispatchEvent(new MouseEvent('mouseenter'));
    popup()?.dispatchEvent(new MouseEvent('mouseleave'));

    vi.advanceTimersByTime(GRACE - 50);
    expect(popup()).not.toBeNull();
    vi.advanceTimersByTime(100);
    expect(popup()).toBeNull();
  });

  it('stays open when the pointer returns before the grace period expires', () => {
    const { marker } = setup();
    marker.dispatchEvent(new MouseEvent('mouseenter'));
    marker.dispatchEvent(new MouseEvent('mouseleave'));
    vi.advanceTimersByTime(GRACE - 40);
    marker.dispatchEvent(new MouseEvent('mouseenter'));

    vi.advanceTimersByTime(1000);
    expect(popup()).not.toBeNull();
  });

  it('steps from the marker into the action with the keyboard', () => {
    // The popup is a sibling of the marker, not a child, so Tab does not reach it on
    // its own.
    const { marker } = setup();
    marker.focus();
    marker.dispatchEvent(new FocusEvent('focus'));

    const event = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true });
    marker.dispatchEvent(event);

    expect(event.defaultPrevented).toBe(true);
    expect(document.activeElement).toBe(action());
  });

  it('also steps in with ArrowDown, and not with Shift+Tab', () => {
    const { marker } = setup();
    marker.focus();
    marker.dispatchEvent(new FocusEvent('focus'));

    marker.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', cancelable: true }));
    expect(document.activeElement).toBe(action());

    // Shift+Tab is how the operator leaves; it must not be hijacked.
    marker.focus();
    const back = new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, cancelable: true });
    marker.dispatchEvent(back);
    expect(back.defaultPrevented).toBe(false);
  });

  it('will not step into a disabled action', () => {
    const { marker } = setup([candidate(1, { revisitAvailable: false })]);
    marker.focus();
    marker.dispatchEvent(new FocusEvent('focus'));
    marker.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', cancelable: true }));

    expect(document.activeElement).toBe(marker);
  });

  it('keeps the popup open while focus is inside it', () => {
    const { marker } = setup();
    marker.focus();
    marker.dispatchEvent(new FocusEvent('focus'));
    marker.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', cancelable: true }));

    vi.advanceTimersByTime(1000);
    expect(popup()).not.toBeNull();
  });

  it('Escape inside the popup closes it and returns focus to the marker', () => {
    const { marker } = setup();
    marker.focus();
    marker.dispatchEvent(new FocusEvent('focus'));
    marker.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', cancelable: true }));

    action()?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));

    expect(popup()).toBeNull();
    expect(document.activeElement).toBe(marker);
  });

  it('Escape on the marker closes the popup', () => {
    const { marker } = setup();
    marker.dispatchEvent(new MouseEvent('mouseenter'));
    marker.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(popup()).toBeNull();
  });

  it('keeps a revisit click away from the map and from marker selection', () => {
    const { marker, onRevisit, onSelect } = setup();
    const mapClicks = vi.fn();
    document.body.addEventListener('click', mapClicks);
    marker.dispatchEvent(new MouseEvent('mouseenter'));

    action()?.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));

    expect(onRevisit).toHaveBeenCalledWith('cand-1');
    expect(onSelect).not.toHaveBeenCalled();
    // Without stopPropagation this would reach the map and drop a polygon vertex.
    expect(mapClicks).not.toHaveBeenCalled();
    document.body.removeEventListener('click', mapClicks);
  });

  it('keeps a click on the popup body away from the map', () => {
    // Not just the button. Clicking the snapshot while drawing a search area would
    // otherwise drop a polygon vertex underneath it.
    const { marker } = setup();
    const mapClicks = vi.fn();
    document.body.addEventListener('click', mapClicks);
    marker.dispatchEvent(new MouseEvent('mouseenter'));

    document.querySelector('.detect-popup__thumb')
      ?.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));

    expect(mapClicks).not.toHaveBeenCalled();
    document.body.removeEventListener('click', mapClicks);
  });

  it('keeps a mousedown in the popup from starting a map drag', () => {
    const { marker } = setup();
    const mapDown = vi.fn();
    document.body.addEventListener('mousedown', mapDown);
    marker.dispatchEvent(new MouseEvent('mouseenter'));

    action()?.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));

    expect(mapDown).not.toHaveBeenCalled();
    document.body.removeEventListener('mousedown', mapDown);
  });

  it('re-renders an open popup when mission permission changes', () => {
    const { layer, marker } = setup();
    marker.dispatchEvent(new MouseEvent('mouseenter'));
    expect(action()?.disabled).toBe(false);

    layer.setRevisitPolicy({ missionAllows: false, missionReason: 'Returning home.' });

    expect(action()?.disabled).toBe(true);
    expect(action()?.title).toBe('Returning home.');
  });

  it('cancels a pending close on teardown', () => {
    const { layer, marker } = setup();
    marker.dispatchEvent(new MouseEvent('mouseenter'));
    marker.dispatchEvent(new MouseEvent('mouseleave'));

    layer.destroy();

    expect(vi.getTimerCount()).toBe(0);
    expect(popup()).toBeNull();
    expect(document.querySelector('.detect-marker')).toBeNull();
  });

  it('cancels a pending close when the candidate disappears', () => {
    const { layer, marker } = setup();
    marker.dispatchEvent(new MouseEvent('mouseenter'));
    marker.dispatchEvent(new MouseEvent('mouseleave'));

    layer.sync([]);

    expect(vi.getTimerCount()).toBe(0);
    expect(popup()).toBeNull();
  });
});
