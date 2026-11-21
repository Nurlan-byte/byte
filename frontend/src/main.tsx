import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { WorkspaceAccess } from "./WorkspaceAccess";
import "./style.css";
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <WorkspaceAccess><App /></WorkspaceAccess>
  </React.StrictMode>,
);
