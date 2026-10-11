/**
 * The entry point.
 *
 * `src/content/disclosure.ts` is imported transitively through `App`, so a disclosure that
 * is missing or empty fails here — at module evaluation, before a single frame renders —
 * rather than at the moment an operator needed to read it.
 */

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./index.css";

const container = document.getElementById("root");
if (container === null) {
  throw new Error(
    "index.html has no #root element; the dashboard has nowhere to mount.",
  );
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
