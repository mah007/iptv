import "@smart-iptv/ui/fonts";
import "./styles.css";

import { installCspNonce } from "@smart-iptv/ui";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { z } from "zod";

import { App, createAppDependencies } from "./app";

const container = document.getElementById("root");
if (!container) throw new Error("Missing #root element in index.html");

// The page's CSP nonce for libraries that add styles at run time (ADR-0021).
installCspNonce();
// No eval under the strict CSP: zod would otherwise probe `new Function` for its fast path.
z.config({ jitless: true });
const dependencies = createAppDependencies();

createRoot(container).render(
  <StrictMode>
    <App {...dependencies} />
  </StrictMode>,
);
