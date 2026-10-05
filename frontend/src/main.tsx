// SPDX-License-Identifier: Apache-2.0
import React from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/inter";
import "@fontsource-variable/source-serif-4";
import { api, captureToken } from "./api";
import { applyBackendLanguage } from "./i18n";
import App from "./App";
import "./styles.css";

captureToken();
// A file dropped outside a drop zone must not make the webview navigate away from the app.
for (const ev of ["dragover", "drop"]) window.addEventListener(ev, (e) => e.preventDefault());

function render() {
  createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
}

// The language lives in the backend's settings (the CLI, settings.json and the desktop shell all
// agree on it); localStorage is only the first-paint guess, since the desktop app has a new origin
// (random port) on every launch. Wait at most 1.5 s for it, then render either way.
Promise.race([api.health(), new Promise<null>((resolve) => setTimeout(() => resolve(null), 1500))])
  .then((h) => { if (h && h.language) applyBackendLanguage(h.language); })
  .catch(() => undefined)
  .finally(render);
