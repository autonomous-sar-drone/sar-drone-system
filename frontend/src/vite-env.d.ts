/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_EVENTS_URL?: string;
  readonly VITE_TELEMETRY_STALE_MS?: string;
  /** URL of a MapLibre style.json. Unset = development-only online tiles. */
  readonly VITE_MAP_STYLE_URL?: string;
  /** "lat,lon" the map opens on before telemetry arrives. */
  readonly VITE_MAP_CENTER?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
