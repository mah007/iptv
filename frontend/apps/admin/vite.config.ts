import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// In the dev stack, Traefik serves this app at admin.<DOMAIN> on HTTP_PORT;
// the HMR websocket must go back through that same port.
const domain = process.env.DOMAIN ?? "localhost";
const hmrClientPort = process.env.VITE_HMR_CLIENT_PORT;

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: true,
    port: 5173,
    strictPort: true,
    allowedHosts: [`admin.${domain}`],
    ...(hmrClientPort ? { hmr: { clientPort: Number(hmrClientPort) } } : {}),
  },
  test: { environment: "jsdom" },
});
