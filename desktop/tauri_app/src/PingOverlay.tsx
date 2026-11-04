import { useEffect, useRef } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { Wheel } from "./ping/renderer/overlay/wheel";
import { emoteFxContent } from "./ping/renderer/shared/emoteFx";
import { EMOTE_DURATION_S } from "./ping/shared/emotes";
import { dataUrl, prepareEmote } from "./ping/renderer/settings/emoteImport";
import { imageSize, sniffImage } from "./ping/shared/imageSniff";
import { builtInMarkers, defaultPreferences, emptyIcon, playBuiltInSound, playMarkerSound, readPreferences, soundIcon, type StoredPreferences } from "./ping/preferences";
import "./ping/renderer/shared/emoteFx.css";
import "./ping-overlay.css";

type Point = { x: number; y: number };
type Marker = Point & { kind: string; image?: string; fx?: number };
type OverlayEvent =
  | ({ type: "wheelOpen" | "wheelMove" | "wheelRelease" | "click" } & Point)
  | { type: "close" | "clear" | "preferences" }
  | { type: "marker"; ping: Marker };

function sharedImage(url: string | undefined): string | undefined {
  if (!url || url.length > 16_420 || !/^data:image\/(?:webp|png);base64,[A-Za-z0-9+/]+=*$/.test(url)) return;
  try {
    const bytes = Uint8Array.from(atob(url.split(",")[1]), char => char.charCodeAt(0));
    const size = imageSize(bytes);
    if (size && size.width <= 160 && size.height <= 160) return url;
  } catch {
    return;
  }
}

const preparedIcons = new Map<string, Promise<string>>();

function prepareSharedIcon(url: string): Promise<string> {
  const cached = preparedIcons.get(url);
  if (cached) return cached;
  const prepared = (async () => {
    const response = await fetch(url);
    if (!response.ok) throw new Error("无法读取轮盘图片");
    const file = new File([await response.blob()], "marker.png");
    const result = await prepareEmote(file);
    if (!result.ok) throw new Error(`无法准备轮盘图片（${result.error}）`);
    const { still } = result.file;
    return dataUrl(still, `image/${sniffImage(still)}`);
  })().catch(error => {
    preparedIcons.delete(url);
    throw error;
  });
  preparedIcons.set(url, prepared);
  return prepared;
}

export default function PingOverlay() {
  const pingSvg = useRef<SVGSVGElement>(null);
  const stage = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!pingSvg.current || !stage.current) return;
    const layer = stage.current;
    const svg = pingSvg.current;
    svg.replaceChildren();
    const wheel = new Wheel(svg, "标记", emptyIcon, () => {});
    let disposed = false;
    let prefs = defaultPreferences;
    let configRequest = 0;
    const timers = new Set<number>();

    const apply = async () => {
      const request = ++configRequest;
      const stored = await invoke<StoredPreferences>("ping_preferences");
      if (disposed || request !== configRequest) return;
      prefs = readPreferences(stored);
      wheel.cancel();
      const item = (key: string) => {
        const builtIn = builtInMarkers.find(marker => marker.id === key);
        const custom = prefs.custom.find(marker => marker.id === key);
        return { key, icon: builtIn?.icon ?? custom?.still ?? (custom?.sound ? soundIcon : emptyIcon),
          label: builtIn?.name ?? custom?.name };
      };
      wheel.setItems(prefs.wheel.map(item));
      wheel.setCenterIcon(item(prefs.clickMarker).icon);
      wheel.setSize(prefs.wheelSize);
    };

    const render = (marker: Marker, localImage?: string) => {
      const icon = builtInMarkers.find(item => item.id === marker.kind)?.icon;
      const url = marker.kind === "emote" ? localImage ?? sharedImage(marker.image) : icon;
      if (!url) return;
      const element = document.createElement("div");
      const emote = marker.kind === "emote";
      const seconds = emote ? EMOTE_DURATION_S : prefs.duration;
      element.style.left = `${marker.x * 100}%`;
      element.style.top = `${marker.y * 100}%`;
      if (emote) {
        element.className = "emote";
        element.style.setProperty("--size", `${prefs.markerSize}px`);
        element.append(emoteFxContent(marker.fx ?? 0, url));
      } else {
        element.className = "beacon-ping-marker";
        element.style.width = element.style.height = `${prefs.markerSize}px`;
        element.style.animationDuration = `${seconds}s`;
        const image = document.createElement("img");
        image.src = url;
        image.alt = "";
        element.append(image);
      }
      if (layer.childElementCount >= 16) layer.firstElementChild?.remove();
      layer.append(element);
      const timer = window.setTimeout(() => {
        timers.delete(timer);
        element.remove();
      }, seconds * 1000);
      timers.add(timer);
    };

    const place = async (key: string, point: Point) => {
      const builtIn = builtInMarkers.find(item => item.id === key);
      const custom = prefs.custom.find(item => item.id === key);
      if (!builtIn && !custom) return;
      const icon = builtIn?.icon ?? builtInMarkers.find(item => item.id === "attention")?.icon ?? emptyIcon;
      const marker = { ...point, kind: "emote", fx: custom?.fx ?? 0 };
      render(marker, custom?.animation ?? custom?.still ?? icon);
      if (builtIn) playBuiltInSound(builtIn.frequency, prefs.volume);
      if (custom?.sound) playMarkerSound(custom.sound, prefs.volume);
      const image = custom?.still ?? await prepareSharedIcon(icon);
      if (disposed) return;
      await invoke("bridge_action", {
        action: "send-ping", payload: { ...marker, image, time: Date.now() / 1000 },
      });
    };

    const subscription = listen<OverlayEvent>("beacon-ping-overlay", ({ payload }) => {
      if (disposed) return;
      if (payload.type === "preferences") {
        void apply().catch(console.error);
      } else if (payload.type === "close" || payload.type === "clear") {
        wheel.cancel();
        if (payload.type === "clear") layer.replaceChildren();
      } else if (payload.type === "marker") {
        render(payload.ping);
        const builtIn = builtInMarkers.find(item => item.id === payload.ping.kind);
        if (builtIn) playBuiltInSound(builtIn.frequency, prefs.volume);
      } else if ("x" in payload) {
        const x = payload.x * window.innerWidth;
        const y = payload.y * window.innerHeight;
        if (payload.type === "wheelOpen") {
          wheel.open(x, y);
        } else if (payload.type === "wheelMove") {
          wheel.move(x, y);
        } else if (payload.type === "wheelRelease") {
          const choice = wheel.release(x, y);
          if (choice) void place(choice.key, {
            x: choice.x / window.innerWidth, y: choice.y / window.innerHeight,
          }).catch(console.error);
        } else {
          void place(prefs.clickMarker, { x: payload.x, y: payload.y }).catch(console.error);
        }
      }
    });
    void subscription.then(async () => {
      if (disposed) return;
      await apply();
      if (!disposed) await invoke("start_ping_overlay");
    }).catch(console.error);
    return () => {
      disposed = true;
      void subscription.then(unlisten => unlisten()).catch(console.error);
      timers.forEach(timer => window.clearTimeout(timer));
      layer.replaceChildren();
    };
  }, []);

  return <>
    <div ref={stage} className="beacon-marker-stage"/>
    <svg ref={pingSvg} id="wheel" viewBox="-300 -300 600 600"/>
  </>;
}
