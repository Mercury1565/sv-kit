/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

// The built UI is committed into the Python package so end users never need Node.
const outDir = fileURLToPath(new URL("../src/svkit/server/static", import.meta.url));
const backend = "http://127.0.0.1:8470";

// netlistsvg (the schematic renderer, run in a Web Worker) and its elkjs 0.3.0 require Node-only modules.
const shim = (f: string) => fileURLToPath(new URL(`./shims/${f}`, import.meta.url));

export default defineConfig({
  plugins: [react()],
  base: "./",
  resolve: {
    alias: { assert: shim("assert.cjs"), stream: shim("stream.cjs"), "webworker-threads": shim("webworker-threads.cjs") },
  },
  worker: { format: "es" },
  build: {
    outDir,
    emptyOutDir: true,
    target: "es2020",
    sourcemap: false,
    // the schematic worker (netlistsvg + elkjs) is one ~1.5 MB chunk loaded only by the Design view.
    chunkSizeWarningLimit: 1800,
    commonjsOptions: { include: [/node_modules/, /shims\//] },
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
