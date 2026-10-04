import "@smart-iptv/ui/fonts";
import "./styles.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App, createAppDependencies } from "./app";

const container = document.getElementById("root");
if (!container) throw new Error("Missing #root element in index.html");

const dependencies = createAppDependencies();

createRoot(container).render(
  <StrictMode>
    <App {...dependencies} />
  </StrictMode>,
);
