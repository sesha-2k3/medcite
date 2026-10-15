/// <reference types="vitest/config" />
import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": path.resolve(import.meta.dirname, "./src") },
  },
  server: { port: 5173, strictPort: true },
  test: {
    include: ["tests/**/*.test.ts", "tests/**/*.test.tsx"],
    environment: "jsdom",
    setupFiles: ["tests/setup.ts"],
    // Hermetic: tests never depend on the developer's frontend/.env.
    env: { VITE_API_BASE_URL: "http://api.test", VITE_AUTH_PROVIDER: "dev", VITE_DEV_TOKEN: "" },
  },
});
