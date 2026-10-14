import { cspPlugin } from "@smart-iptv/ui/csp";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// In the dev stack, Traefik serves this app at app.<DOMAIN> on HTTP_PORT;
// the HMR websocket must go back through that same port.
const domain = process.env.DOMAIN ?? "localhost";
const hmrClientPort = process.env.VITE_HMR_CLIENT_PORT;
// The production CSP in development too (ADR-0021): the media edge and every subdomain
// on the dev port, plus the HMR websocket back through Traefik.
const port = hmrClientPort && hmrClientPort !== "80" ? `:${hmrClientPort}` : "";
const csp = cspPlugin("portal", {
  mediaOrigins: [`http://media.${domain}${port}`, `http://*.${domain}${port}`],
  hmr: [`ws://app.${domain}${port}`],
});

export default defineConfig({
  plugins: [react(), tailwindcss(), csp],
  server: {
    host: true,
    port: 5174,
    strictPort: true,
    allowedHosts: [`app.${domain}`],
    ...(hmrClientPort ? { hmr: { clientPort: Number(hmrClientPort) } } : {}),
  },
  build: {
    // Shaka's HLS build is ~520 kB on its own; it loads with the player page only.
    chunkSizeWarningLimit: 560,
    rolldownOptions: {
      output: {
        codeSplitting: {
          // React changes rarely: its own long-cached chunk survives app releases.
          // Shaka loads only with the player page.
          groups: [
            { name: "react", test: /[\\/]node_modules[\\/](react|react-dom|scheduler)[\\/]/ },
            { name: "shaka", test: /[\\/]node_modules[\\/]shaka-player[\\/]/ },
          ],
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    // The browser journey (e2e/) runs under Playwright, not Vitest.
    include: ["src/**/*.test.{ts,tsx}"],
    // The dev container is capped at 3 GB and the admin tests run beside these.
    maxWorkers: 2,
  },
});
