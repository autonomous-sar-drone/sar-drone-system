import type { StyleSpecification } from 'maplibre-gl';

/**
 * DEVELOPMENT ONLY. Public OpenStreetMap raster tiles need internet, and the production SAR
 * console must work offline (handoff section 8). Point VITE_MAP_STYLE_URL at a locally served
 * MapLibre style (for example from FastAPI) to replace this without touching map code.
 * The background layer keeps the map usable, just blank, when tiles can't load.
 */
const DEV_ONLINE_STYLE: StyleSpecification = {
  version: 8,
  sources: {
    'dev-osm': {
      type: 'raster',
      tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
      tileSize: 256,
      maxzoom: 19,
      attribution: '© OpenStreetMap contributors (development tiles)',
    },
  },
  layers: [
    { id: 'background', type: 'background', paint: { 'background-color': '#e8ebe9' } },
    { id: 'dev-osm', type: 'raster', source: 'dev-osm' },
  ],
};

export function mapStyle(): string | StyleSpecification {
  return import.meta.env.VITE_MAP_STYLE_URL || DEV_ONLINE_STYLE;
}

/** [longitude, latitude] the map opens on before telemetry arrives. */
export function initialCenter(): [number, number] {
  const raw = import.meta.env.VITE_MAP_CENTER;
  if (raw) {
    const [lat, lon] = raw.split(',').map(Number);
    if (Number.isFinite(lat) && Number.isFinite(lon)) return [lon, lat];
  }
  return [-97.7555, 30.2291];
}

export const INITIAL_ZOOM = 16;

/** Pixel radius for snapping to the first vertex to close the polygon. */
export const SNAP_PX = 14;

/**
 * Layer colors. Widths are in screen pixels so lines stay readable at every zoom.
 * Search area (orange) and route (blue) are deliberately far apart in hue.
 */
export const MAP_COLORS = {
  area: '#e8481c',
  areaError: '#c62828',
  blocked: '#b0002a',
  route: '#1f6feb',
  casing: '#ffffff',
  drone: '#15202b',
  routeStart: '#17784a',
} as const;
