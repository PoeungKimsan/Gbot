/**
 * Vite, Tailwind and Vitest.
 *
 * 1. **`base: "./"` and `outDir: "dist"`.** `api/main.py` mounts `web/dist` into its own app,
 *   so the bundle has to be relocatable: absolute asset paths (`/assets/…`) would 404 the
 *   moment the mount prefix changed.
 *
 * 2. **A dev-server proxy for `/api`.** REST calls go to `127.0.0.1:8080`, the API's own bind.
 */

import { defineConfig } from "vitest/config";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [tailwindcss(), react()],

  // Relocatable assets, because the API mounts this directory.
  base: "./",

  server: {
    port: 5173,
    strictPort: false,
    proxy: {
      // The dashboard's only I/O: relative /api/* paths, forwarded to the read-only API.
      "/api": {
        target: "http://127.0.0.1:8080",
        changeOrigin: true,
      },
      "/ws": {
        target: "ws://127.0.0.1:8080",
        ws: true,
      },
    },
  },

  preview: {
    port: 4173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8080",
        changeOrigin: true,
      },
    },
  },

  build: {
    // Relative to this file's directory, so the output is web/dist.
    outDir: "dist",
    emptyOutDir: true,
    target: "es2022",
    sourcemap: false,
    // The dashboard is one screen; chunk-splitting it buys nothing and costs a round trip.
    assetsInlineLimit: 0,
    reportCompressedSize: true,
  },

  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    passWithNoTests: true,
    // CSS is stubbed: the suite asserts structure and text, not Tailwind's output.
    css: false,
    restoreMocks: true,
  },
});
