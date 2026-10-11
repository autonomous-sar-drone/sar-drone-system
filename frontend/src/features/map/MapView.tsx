import type { Feature, FeatureCollection } from 'geojson';
import * as maplibregl from 'maplibre-gl';
import type { GeoJSONSource, MapMouseEvent } from 'maplibre-gl';
// MapLibre 6 locates its worker relative to its own file, which breaks once Vite bundles it.
// Let Vite build the worker (with its shared chunk) and hand MapLibre the resulting URL.
import maplibreWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';
import 'maplibre-gl/dist/maplibre-gl.css';
import { useEffect, useRef, useState } from 'react';
import type { Route } from '../../types/routes';
import type { EditMode } from '../planning/planningState';
import { canAppendVertex, canClose, type LatLon } from '../searchArea/geometry';
import { INITIAL_ZOOM, MAP_COLORS, SNAP_PX, initialCenter, mapStyle } from './mapConfig';

export interface MapViewProps {
  vertices: LatLon[];
  closed: boolean;
  mode: EditMode;
  selectedIndex: number | null;
  /** Closed polygon currently fails a local check (e.g. a drag made edges cross). */
  shapeInvalid: boolean;
  /** Read-only. Fed from planning state's currentRoute, never edited here. */
  route: Route | null;
  drone: LatLon | null;
  /** Bump to re-center on the drone. */
  centerOnDroneRequest: number;
  onAddVertex: (point: LatLon) => void;
  onClose: () => void;
  onMoveVertex: (index: number, point: LatLon) => void;
  onSelectVertex: (index: number | null) => void;
}

type FC = FeatureCollection;
const EMPTY: FC = { type: 'FeatureCollection', features: [] };
maplibregl.setWorkerUrl(maplibreWorkerUrl);

const toLatLon = (l: maplibregl.LngLat): LatLon => ({ latitudeDeg: l.lat, longitudeDeg: l.lng });
const toCoord = (p: LatLon): [number, number] => [p.longitudeDeg, p.latitudeDeg];

/** Each concern is its own source + layers so later coverage/trail layers slot in beside them. */
const SOURCES = ['search-area', 'route', 'draw-preview', 'vertices', 'drone'] as const;

function addLayers(map: maplibregl.Map) {
  for (const id of SOURCES) map.addSource(id, { type: 'geojson', data: EMPTY });
  const areaColor: maplibregl.ExpressionSpecification = [
    'case', ['get', 'invalid'], MAP_COLORS.areaError, MAP_COLORS.area,
  ];

  map.addLayer({
    id: 'search-area-fill', type: 'fill', source: 'search-area',
    filter: ['==', ['geometry-type'], 'Polygon'],
    paint: { 'fill-color': areaColor, 'fill-opacity': 0.12 },
  });
  map.addLayer({
    id: 'search-area-line', type: 'line', source: 'search-area',
    layout: { 'line-join': 'round', 'line-cap': 'round' },
    paint: { 'line-color': areaColor, 'line-width': 2.5 },
  });
  // Route sits above the search area, with a white casing so it reads on any basemap.
  map.addLayer({
    id: 'route-casing', type: 'line', source: 'route',
    filter: ['==', ['geometry-type'], 'LineString'],
    layout: { 'line-join': 'round', 'line-cap': 'round' },
    paint: { 'line-color': MAP_COLORS.casing, 'line-width': 6 },
  });
  map.addLayer({
    id: 'route-line', type: 'line', source: 'route',
    filter: ['==', ['geometry-type'], 'LineString'],
    layout: { 'line-join': 'round', 'line-cap': 'round' },
    paint: { 'line-color': MAP_COLORS.route, 'line-width': 3 },
  });
  map.addLayer({
    id: 'route-start', type: 'circle', source: 'route',
    filter: ['==', ['geometry-type'], 'Point'],
    paint: {
      'circle-radius': 6, 'circle-color': MAP_COLORS.routeStart,
      'circle-stroke-color': MAP_COLORS.casing, 'circle-stroke-width': 2,
    },
  });
  // Legal next segment: thin dashed orange. Illegal crossing: thick solid red with a casing,
  // so it reads as "blocked" at a glance rather than as a slightly different orange.
  map.addLayer({
    id: 'draw-preview', type: 'line', source: 'draw-preview',
    filter: ['==', ['get', 'valid'], true],
    layout: { 'line-cap': 'round' },
    paint: { 'line-color': MAP_COLORS.area, 'line-width': 2.5, 'line-dasharray': [2, 1.5] },
  });
  map.addLayer({
    id: 'draw-preview-blocked-casing', type: 'line', source: 'draw-preview',
    filter: ['==', ['get', 'valid'], false],
    layout: { 'line-cap': 'round' },
    paint: { 'line-color': MAP_COLORS.casing, 'line-width': 7 },
  });
  map.addLayer({
    id: 'draw-preview-blocked', type: 'line', source: 'draw-preview',
    filter: ['==', ['get', 'valid'], false],
    layout: { 'line-cap': 'round' },
    paint: { 'line-color': MAP_COLORS.blocked, 'line-width': 4 },
  });
  map.addLayer({
    id: 'vertex-handles', type: 'circle', source: 'vertices',
    paint: {
      'circle-radius': ['case', ['get', 'selected'], 8, ['get', 'snapTarget'], 9, 6],
      'circle-color': ['case', ['get', 'selected'], MAP_COLORS.area, MAP_COLORS.casing],
      'circle-stroke-color': MAP_COLORS.area,
      'circle-stroke-width': 2.5,
    },
  });
  map.addLayer({
    id: 'drone', type: 'circle', source: 'drone',
    paint: {
      'circle-radius': 7, 'circle-color': MAP_COLORS.drone,
      'circle-stroke-color': MAP_COLORS.casing, 'circle-stroke-width': 2.5,
    },
  });
}

function setData(map: maplibregl.Map, id: (typeof SOURCES)[number], data: FC) {
  (map.getSource(id) as GeoJSONSource | undefined)?.setData(data);
}

export function MapView(props: MapViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const propsRef = useRef(props);
  const dragIndexRef = useRef<number | null>(null);
  const cursorRef = useRef<{ point: maplibregl.Point; lngLat: maplibregl.LngLat } | null>(null);
  const snappingRef = useRef(false);
  const userMovedRef = useRef(false);
  const centeredOnDroneRef = useRef(false);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    propsRef.current = props;
  });

  /** Live segment from the newest vertex to the cursor, red when it can't be committed. */
  function updatePreview() {
    const map = mapRef.current;
    const { mode, vertices } = propsRef.current;
    const cursor = cursorRef.current;
    snappingRef.current = false;
    if (!map || mode !== 'drawing' || vertices.length === 0 || !cursor) {
      if (map?.getSource('draw-preview')) setData(map, 'draw-preview', EMPTY);
      updateVertices();
      return;
    }
    const last = vertices[vertices.length - 1];
    let end = toLatLon(cursor.lngLat);
    let valid = canAppendVertex(vertices, end);
    if (vertices.length >= 3) {
      const firstPx = map.project(toCoord(vertices[0]));
      if (Math.hypot(firstPx.x - cursor.point.x, firstPx.y - cursor.point.y) <= SNAP_PX) {
        snappingRef.current = true;
        end = vertices[0];
        valid = canClose(vertices);
      }
    }
    setData(map, 'draw-preview', {
      type: 'FeatureCollection',
      features: [{
        type: 'Feature', properties: { valid },
        geometry: { type: 'LineString', coordinates: [toCoord(last), toCoord(end)] },
      }],
    });
    map.getCanvas().style.cursor = snappingRef.current ? 'pointer' : valid ? 'crosshair' : 'not-allowed';
    updateVertices();
  }

  function updateVertices() {
    const map = mapRef.current;
    if (!map?.getSource('vertices')) return;
    const { mode, vertices, selectedIndex } = propsRef.current;
    const show = mode === 'drawing' || mode === 'editing';
    setData(map, 'vertices', {
      type: 'FeatureCollection',
      features: show
        ? vertices.map((v, index) => ({
            type: 'Feature',
            properties: {
              index,
              selected: index === selectedIndex,
              snapTarget: index === 0 && snappingRef.current,
            },
            geometry: { type: 'Point', coordinates: toCoord(v) },
          }))
        : [],
    });
  }

  function vertexAt(point: maplibregl.Point): number | null {
    const map = mapRef.current;
    if (!map) return null;
    const hit = map.queryRenderedFeatures(point, { layers: ['vertex-handles'] })[0];
    const index = hit?.properties?.index;
    return typeof index === 'number' ? index : null;
  }

  // Create the map once.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const map = new maplibregl.Map({
      container,
      style: mapStyle(),
      center: initialCenter(),
      zoom: INITIAL_ZOOM,
      attributionControl: { compact: true },
    });
    mapRef.current = map;
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
    map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-left');

    // style.load fires once the style is parsed, without waiting for basemap tiles. With no
    // internet (or a slow tile server) 'load' can be delayed indefinitely, and the operator must
    // still be able to draw.
    map.once('style.load', () => {
      addLayers(map);
      setReady(true);
    });

    const markUserMoved = (e: { originalEvent?: unknown }) => {
      if (e.originalEvent) userMovedRef.current = true;
    };
    map.on('dragstart', markUserMoved);
    map.on('zoomstart', markUserMoved);

    map.on('mousemove', (e: MapMouseEvent) => {
      cursorRef.current = { point: e.point, lngLat: e.lngLat };
      const { mode, onMoveVertex } = propsRef.current;
      if (dragIndexRef.current !== null) {
        onMoveVertex(dragIndexRef.current, toLatLon(e.lngLat));
        return;
      }
      if (mode === 'drawing') {
        updatePreview();
      } else if (mode === 'editing') {
        map.getCanvas().style.cursor = vertexAt(e.point) !== null ? 'grab' : '';
      } else {
        map.getCanvas().style.cursor = '';
      }
    });

    map.getCanvas().addEventListener('mouseleave', () => {
      cursorRef.current = null;
      updatePreview();
    });

    // MapLibre does not fire click after a drag-pan, which gives us click-to-place / drag-to-pan.
    map.on('click', (e: MapMouseEvent) => {
      const { mode, vertices, onAddVertex, onClose, onSelectVertex } = propsRef.current;
      if (mode === 'drawing') {
        cursorRef.current = { point: e.point, lngLat: e.lngLat };
        updatePreview();
        if (snappingRef.current) {
          if (canClose(vertices)) onClose();
          return;
        }
        const point = toLatLon(e.lngLat);
        if (canAppendVertex(vertices, point)) onAddVertex(point);
      } else if (mode === 'editing') {
        onSelectVertex(vertexAt(e.point));
      }
    });

    map.on('mousedown', (e: MapMouseEvent) => {
      if (propsRef.current.mode !== 'editing') return;
      const index = vertexAt(e.point);
      if (index === null) return;
      e.preventDefault(); // stop the map from panning while a vertex is dragged
      dragIndexRef.current = index;
      map.dragPan.disable();
      map.getCanvas().style.cursor = 'grabbing';
      propsRef.current.onSelectVertex(index);
    });

    const endDrag = () => {
      if (dragIndexRef.current === null) return;
      dragIndexRef.current = null;
      map.dragPan.enable();
      map.getCanvas().style.cursor = '';
    };
    window.addEventListener('mouseup', endDrag);

    const resize = new ResizeObserver(() => map.resize());
    resize.observe(container);

    return () => {
      window.removeEventListener('mouseup', endDrag);
      resize.disconnect();
      map.remove();
      mapRef.current = null;
    };
  }, []);

  const { vertices, closed, mode, selectedIndex, shapeInvalid, route, drone, centerOnDroneRequest } =
    props;

  // Search-area polygon (closed) or polyline (still drawing).
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const coords = vertices.map(toCoord);
    let features: Feature[] = [];
    if (closed && coords.length >= 3) {
      features = [{
        type: 'Feature', properties: { invalid: shapeInvalid },
        geometry: { type: 'Polygon', coordinates: [[...coords, coords[0]]] },
      }];
    } else if (coords.length >= 2) {
      features = [{
        type: 'Feature', properties: { invalid: false },
        geometry: { type: 'LineString', coordinates: coords },
      }];
    }
    setData(map, 'search-area', { type: 'FeatureCollection', features });
    updatePreview();
    // Panning to draw on a map that can't be double-click zoomed avoids accidental zooms mid-polygon.
    if (mode === 'drawing') map.doubleClickZoom.disable();
    else map.doubleClickZoom.enable();
    if (mode !== 'drawing' && mode !== 'editing') map.getCanvas().style.cursor = '';
    // updatePreview reads refs; listing it would recreate the effect every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, vertices, closed, mode, selectedIndex, shapeInvalid]);

  // Route layer: renders whatever currentRoute holds (preview now, live route_update later).
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    if (!route || route.waypoints.length === 0) {
      setData(map, 'route', EMPTY);
      return;
    }
    const coords = route.waypoints.map(toCoord);
    setData(map, 'route', {
      type: 'FeatureCollection',
      features: [
        { type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: coords } },
        { type: 'Feature', properties: { role: 'start' }, geometry: { type: 'Point', coordinates: coords[0] } },
      ],
    });
  }, [ready, route]);

  // Drone marker from telemetry_update.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    setData(map, 'drone', drone
      ? { type: 'FeatureCollection', features: [{ type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: toCoord(drone) } }] }
      : EMPTY);
    // First fix: bring the drone into view, but never move the map under an operator who has
    // already panned or is planning (a jump mid-drawing would shift every vertex on screen).
    const planning = propsRef.current.mode !== 'none';
    if (drone && !centeredOnDroneRef.current && !userMovedRef.current && !planning) {
      centeredOnDroneRef.current = true;
      map.jumpTo({ center: toCoord(drone) });
    }
  }, [ready, drone]);

  useEffect(() => {
    const map = mapRef.current;
    const target = propsRef.current.drone;
    if (!map || centerOnDroneRequest === 0 || !target) return;
    map.easeTo({ center: toCoord(target), duration: 400 });
  }, [centerOnDroneRequest]);

  return <div ref={containerRef} className="map-view" data-testid="map-view" />;
}
