import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Long-lived vendor chunks, so a deploy that only touches app code keeps them cached, and so the
// three.js stack never lands in the chunks the first paint needs (it is only reachable through the
// lazy PondScene import in pages/Home.tsx).
const THREE_STACK = [
  "/node_modules/three/",
  "/node_modules/@react-three/", // includes fiber's nested scheduler/zustand copies
  "/node_modules/react-reconciler/",
  "/node_modules/its-fine/",
  "/node_modules/suspend-react/",
  "/node_modules/react-use-measure/",
];
const REACT_STACK = [
  "/node_modules/react/",
  "/node_modules/react-dom/",
  "/node_modules/scheduler/",
  "/node_modules/react-router/",
  "/node_modules/react-router-dom/",
  "/node_modules/@remix-run/",
  "/node_modules/zustand/",
];
const MOTION_STACK = ["/node_modules/motion/", "/node_modules/framer-motion/", "/node_modules/motion-dom/", "/node_modules/motion-utils/"];

function vendorChunk(id: string): string | undefined {
  const p = id.split("\\").join("/");
  // CSS stays with its importer: leaflet.css is imported by main.tsx and must keep its place in the
  // entry stylesheet (cascade order) without dragging Leaflet's JS into the first paint.
  if (!p.includes("/node_modules/") || p.endsWith(".css")) return undefined;
  if (THREE_STACK.some((s) => p.includes(s))) return "vendor-three";
  if (REACT_STACK.some((s) => p.includes(s))) return "vendor-react";
  if (MOTION_STACK.some((s) => p.includes(s))) return "vendor-motion";
  if (p.includes("/node_modules/leaflet/")) return "vendor-leaflet";
  return undefined;
}

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: { manualChunks: vendorChunk },
    },
  },
  server: {
    port: 5173,
    // Bind all interfaces so the end-user page can be tested on a phone over a tunnel.
    host: true,
    proxy: {
      "/v1": { target: "http://localhost:8000", changeOrigin: true, ws: true },
    },
  },
});
