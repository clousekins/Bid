import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import "./styles.css";
import "./styles-v2.css";
import "./styles-v3.css";
import "./styles-theme.css";
import "./styles-landing.css";
import "./styles-progress.css";

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
