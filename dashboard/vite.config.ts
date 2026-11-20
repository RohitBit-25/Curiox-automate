import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const target = process.env.API_URL ?? "http://localhost:8000";
const proxy = { "/api": target, "/media": target };

export default defineConfig({
  plugins: [react()],
  server: { proxy },
  preview: { proxy, allowedHosts: true },
});
