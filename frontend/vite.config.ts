import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Bind all interfaces so the end-user page can be tested on a phone over a tunnel.
    host: true,
    proxy: {
      "/v1": { target: "http://localhost:8000", changeOrigin: true, ws: true },
    },
  },
});
