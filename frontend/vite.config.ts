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
});
