import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import TrayPopup from "./TrayPopup";
import "./styles.css";
import "./effects.css";
import "./modal.css";
import "./theme.css";
import "./tray-popup.css";

const trayView = new URLSearchParams(window.location.search).get("view") === "tray";
if (trayView) document.documentElement.classList.add("tray-view");
ReactDOM.createRoot(document.getElementById("root")!).render(<React.StrictMode>{trayView?<TrayPopup/>:<App/>}</React.StrictMode>);
