/**
 * hr_voice_agent/frontend/src/main.jsx
 *
 * React application entry point for the HR Voice Bot web test client.
 */

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import "./index.css";

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <App />
  </StrictMode>
);
