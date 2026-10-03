// SPDX-License-Identifier: Apache-2.0
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run build` writes into the Python package so `rhz serve` and the desktop sidecar serve the UI.
export default defineConfig({
  plugins: [react()],
  base: "/",
  build: { outDir: "../backend/rhizome/web", emptyOutDir: true },
  server: { port: 5173 },
});
