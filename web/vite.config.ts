/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

// The built UI is committed into the Python package so end users never need Node.
const outDir = fileURLToPath(new URL("../src/svkit/server/static", import.meta.url));
const backend = "http://127.0.0.1:8470";

export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    outDir,
    emptyOutDir: true,
    target: "es2020",
    sourcemap: false,
    // elkjs is one ~1.5 MB lazy chunk loaded only by the Design view.
    chunkSizeWarningLimit: 1800,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": backend,
      "/ws": { target: backend, ws: true },
    },
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    setupFiles: ["src/test/setup.ts"],
  },
});
