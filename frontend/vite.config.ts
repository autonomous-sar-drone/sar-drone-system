import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// FastAPI backend (or scripts/mock-events-server.mjs) during development.
const backendUrl = process.env.SAR_BACKEND_URL ?? 'http://localhost:8000';

export default defineConfig({
  plugins: [react()],
  // MapLibre's worker is an ES module; keep it one when Vite bundles it.
  worker: { format: 'es' },
  server: {
    proxy: {
      // Browser connects to ws://<vite-host>/ws/events; Vite forwards it to the backend.
      '/ws': { target: backendUrl, ws: true, changeOrigin: true },
      '/api': { target: backendUrl, changeOrigin: true },
    },
  },
});
