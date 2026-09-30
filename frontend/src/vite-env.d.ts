/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_EVENTS_URL?: string;
  readonly VITE_TELEMETRY_STALE_MS?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
