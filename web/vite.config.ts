/**
 * Vite, Tailwind, Vitest, and one guard.
 *
 * Three decisions worth naming:
 *
 * 1. **`base: "./"` and `outDir: "dist"`.** `api/main.py` mounts `web/dist` into its own app,
 *   so the bundle has to be relocatable: absolute asset paths (`/assets/…`) would 404 the
 *   moment the mount prefix changed. `dist` is relative to this directory, so the output is
 *   `web/dist` — exactly what the Phase 5 spec said would be mounted.
 *
 * 2. **A dev-server proxy for `/api`.** The dashboard consumes only relative endpoints, so
 *   in development they have to go somewhere: `127.0.0.1:8080`, the API's own bind. Nothing
 *   about the dashboard's code knows that host exists; it is the dev server's business.
 *
 * 3. **`xauusd-disclosure-guard`.** The mandatory disclosure at
 *   `src/content/disclosure.txt` must exist and must not be empty, and a build that lost it
 *   has to fail loudly rather than render a performance panel with no notice attached. The
 *   guard reads the file before any module is resolved and raises a named error.
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import type { Plugin } from "vite";
import { defineConfig } from "vitest/config";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";

/** The disclosure, relative to the web root. */
const DISCLOSURE_PATH = "src/content/disclosure.txt";

/**
 * Fail the build if the mandatory disclosure is missing or empty.
 *
 * `buildStart` runs before module resolution in both `vite build` and `vitest`, so this is
 * the error an operator reads instead of an unresolved-import stack trace from `?raw`.
 */
function disclosureGuard(): Plugin {
  let root = process.cwd();

  return {
    name: "xauusd-disclosure-guard",
    enforce: "pre",

    configResolved(resolved) {
      root = resolved.root;
    },

    buildStart() {
      const absolute = path.resolve(root, DISCLOSURE_PATH);
      let text: string;
      try {
        text = readFileSync(absolute, "utf-8");
      } catch (cause) {
        throw new Error(
          `${DISCLOSURE_PATH} is missing; the mandatory disclosure cannot be rendered. ` +
            "A dashboard that shows performance without its disclosure must not build. " +
            `(${String(cause)})`,
          { cause },
        );
      }
      if (text.trim() === "") {
        throw new Error(
          `${DISCLOSURE_PATH} is empty; the mandatory disclosure cannot be rendered. ` +
            "A dashboard that shows performance without its disclosure must not build.",
        );
      }
    },
  };
}

export default defineConfig({
  plugins: [disclosureGuard(), tailwindcss(), react()],

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
    // CSS is stubbed: the suite asserts structure and text, not Tailwind's output.
    css: false,
    restoreMocks: true,
  },
});
