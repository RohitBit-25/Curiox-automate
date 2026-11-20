import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const proxy = { "/api": process.env.API_URL ?? "http://localhost:8000" };

export default defineConfig({
  plugins: [react()],
  server: { proxy },
  preview: { proxy, allowedHosts: true },
});
