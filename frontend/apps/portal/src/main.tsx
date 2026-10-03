import "@smart-iptv/ui/fonts";
import "./styles.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./app";

const container = document.getElementById("root");
if (!container) throw new Error("Missing #root element in index.html");

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
