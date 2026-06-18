import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: { outDir: "../ytdl/web", emptyOutDir: true },
  server: {
    proxy: {
      "/jobs": "http://127.0.0.1:8765",
      "/events": "http://127.0.0.1:8765",
      "/library": "http://127.0.0.1:8765",
      "/preview": "http://127.0.0.1:8765",
    },
  },
  test: { environment: "jsdom", setupFiles: ["./tests/setup.ts"] },
});
