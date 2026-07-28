import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Build output goes into the Python package so `factory ui` serves the SPA
// from one process with no node runtime requirement.
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    outDir: "../src/factory/web/static",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8500",
    },
  },
});
