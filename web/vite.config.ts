import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Dev: `npm run dev` (port 5173) proxies /api to `uv run ffm serve` (port 8000).
// Prod: `npm run build` -> web/dist, which `ffm serve` serves on one port.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
  build: { outDir: "dist", sourcemap: false, target: "es2022" },
});
