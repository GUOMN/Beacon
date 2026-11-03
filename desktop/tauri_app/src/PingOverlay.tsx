import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import "./ping-overlay.css";

const icons = { danger: "!", push: "↗", omw: "➜", allin: "◆", assist: "+", needvision: "◈", missing: "?", enemyvision: "⊘" };
type Kind = keyof typeof icons;
type Point = { x: number; y: number };
type Ping = Point & { kind: Kind };
type OverlayEvent =
  | ({ type: "open" } & Point)
  | { type: "select"; kind: Kind | null }
  | { type: "close" | "clear" }
  | { type: "marker"; ping: Ping };

const position = (point: Point) => ({
  left: `${point.x * 100}%`,
  top: `${point.y * 100}%`,
});

export default function PingOverlay() {
  const [wheel, setWheel] = useState<Point | null>(null);
  const [selected, setSelected] = useState<Kind | null>(null);
  const [markers, setMarkers] = useState<(Ping & { id: number })[]>([]);

  useEffect(() => {
    let disposed = false;
    let id = 0;
    const timers = new Set<number>();
    const subscription = listen<OverlayEvent>("beacon-ping-overlay", ({ payload }) => {
      if (disposed) return;
      switch (payload.type) {
        case "open":
          setWheel(payload);
          setSelected(null);
          break;
        case "select":
          setSelected(payload.kind);
          break;
        case "clear":
          setMarkers([]);
          setWheel(null);
          break;
        case "close":
          setWheel(null);
          break;
        case "marker": {
          const marker = { ...payload.ping, id: ++id };
          setMarkers(previous => [...previous.slice(-15), marker]);
          const timer = window.setTimeout(() => {
            timers.delete(timer);
            setMarkers(previous => previous.filter(item => item.id !== marker.id));
          }, 1400);
          timers.add(timer);
          break;
        }
      }
    });
    void subscription.then(() => {
      if (!disposed) return invoke("start_ping_overlay");
    }).catch(console.error);
    return () => {
      disposed = true;
      void subscription.then(unlisten => unlisten()).catch(console.error);
      timers.forEach(timer => window.clearTimeout(timer));
    };
  }, []);

  return <>
    {markers.map(marker => <div className={`beacon-ping-marker ping-${marker.kind}`} key={marker.id} style={position(marker)}>{icons[marker.kind]}</div>)}
    {wheel && <div className="beacon-ping-wheel" style={position(wheel)}>
      {(Object.keys(icons) as Kind[]).map((kind, index) => <div key={kind} style={{
        left: `${50 + Math.sin(index * Math.PI / 4) * 34}%`,
        top: `${50 - Math.cos(index * Math.PI / 4) * 34}%`,
      }} className={`ping-${kind}${selected === kind ? " selected" : ""}`}>{icons[kind]}</div>)}
    </div>}
  </>;
}
