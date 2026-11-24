import { defineConfig } from "vite";

// Dev server: the page is served by Vite, the API by the local Python server.
export default defineConfig({
  root: "web",
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8765",
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: "../web-dist",
    emptyOutDir: true,
  },
});
