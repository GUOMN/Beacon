import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import PingOverlay from "./PingOverlay";
import TrayPopup from "./TrayPopup";
import "./styles.css";
import "./effects.css";
import "./modal.css";
import "./theme.css";
import "./tray-popup.css";

const view = new URLSearchParams(window.location.search).get("view");
const trayView = view === "tray";
if (view === "ping") document.documentElement.classList.add("ping-view");
const savedTheme = localStorage.getItem("beacon-theme");
document.documentElement.dataset.theme = savedTheme === "mecha" || savedTheme === "aldnoah" ? savedTheme : "default";
if (trayView) document.documentElement.classList.add("tray-view");
ReactDOM.createRoot(document.getElementById("root")!).render(<React.StrictMode>{view === "ping" ? <PingOverlay/> : trayView ? <TrayPopup/> : <App/>}</React.StrictMode>);
