/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";

// Desarrollo: `python -m factory_ui <proyecto> --token dev --no-browser` y `npm run dev`.
// El proxy manda el token como header; en produccion FastAPI sirve el build y usa cookie.
const token = process.env.FACTORY_TOKEN ?? "dev";
const backend = process.env.FACTORY_BACKEND ?? "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: backend, changeOrigin: true, headers: { "X-Factory-Token": token } },
    },
  },
  build: {
    outDir: "../factory_ui/static",
    emptyOutDir: true,
    chunkSizeWarningLimit: 8000,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
