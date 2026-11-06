import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

/**
 * hr_voice_agent/frontend/vite.config.js
 *
 * Vite build and dev configuration for the HR Voice Bot web test client.
 * Proxies token requests to the local token_server.py running on :8000.
 */
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/getToken":    "http://localhost:8000",
      "/getSipToken": "http://localhost:8000",
      "/health":      "http://localhost:8000",
    },
  },
  build: {
    outDir: "dist",
  },
});
