import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Proxies /api/* and /events to the FastAPI backend on :8000,
      // avoiding CORS entirely during local dev.
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, "") },
      "/events": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
});
