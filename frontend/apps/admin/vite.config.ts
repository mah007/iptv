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
  build: {
    rolldownOptions: {
      output: {
        codeSplitting: {
          // React changes rarely: its own long-cached chunk survives app releases.
          groups: [
            { name: "react", test: /[\\/]node_modules[\\/](react|react-dom|scheduler)[\\/]/ },
          ],
        },
      },
    },
  },
  // At most 4 workers: the dev container is capped at 3 GB and the packages test in parallel.
  test: { environment: "jsdom", setupFiles: ["./src/test-setup.ts"], maxWorkers: 4 },
});
