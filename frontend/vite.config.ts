import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Bind to 0.0.0.0 instead of the localhost-only default, so the dev
    // server is reachable from other devices on the same network (e.g.
    // http://<this-machine's-LAN-IP>:5173 from a phone or tablet).
    host: true,
  },
  build: {
    rollupOptions: {
      output: {
        // Split the React/Router runtime into its own cached chunk so the
        // main entry stays small and vendor code only re-downloads when
        // those deps actually change. recharts is deliberately NOT included
        // here — the summary dashboards are lazy-loaded, so its chart chunk
        // (and heavy deps like @reduxjs/toolkit/victory-vendor) must remain
        // in a separate async chunk fetched only on pages that draw charts.
        manualChunks(id: string) {
          if (!id.includes("node_modules")) return;
          const p = id.split("\\").join("/");
          if (
            p.includes("/react-router") ||
            p.includes("/react-dom/") ||
            p.includes("/react/")
          ) {
            return "react-vendor";
          }
        },
      },
    },
  },
});
