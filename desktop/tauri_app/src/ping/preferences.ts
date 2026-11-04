import { HOTKEY_PRIMARY_MODS, UNSAFE_TRIGGER_VKS, type Hotkey } from "./shared/keys";
import { DEFAULT_EMOJI, emojiId, LEGACY_MARKERS, MARKERS } from "./shared/pings";

export const triggers = {
  "logi-f13": "罗技侧键映射",
  alt: "Alt / Option", ctrl: "Control", shift: "Shift", win: "Win / Command",
  capslock: "Caps Lock", mouse4: "鼠标侧键 4", mouse5: "鼠标侧键 5",
};
export type Trigger = keyof typeof triggers | "vk";
export const defaultToggleHotkey: Hotkey = { mods: 3, vk: 0x50 };
export type CustomMarker = {
  id: string;
  name: string;
  still?: string;
  animation?: string;
  sound?: string;
  fx?: number;
};

export const defaultWheel = DEFAULT_EMOJI.map(emojiId);
export const defaultCenter = emojiId("OK");
export const defaultPreferences = {
  enabled: true, trigger: "alt" as Trigger, triggerVk: 0, toggleHotkey: defaultToggleHotkey, clickPing: false, dragThreshold: 8,
  wheelSize: 420, markerSize: 100, duration: 1.4, volume: 0.5,
  wheel: [...defaultWheel], clickMarker: defaultCenter, custom: [] as CustomMarker[],
};
export type Preferences = typeof defaultPreferences;
export type StoredPreferences = Partial<Preferences> & {
  emoteWheel?: string[];
  emotes?: CustomMarker[];
  clickEmoteId?: string;
  emoteClick?: boolean;
  pingSize?: number;
};

const markerIcon = (art: string, color: string) => `data:image/svg+xml,${encodeURIComponent(
  `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48"><path d="M24 2 42 12v24L24 46 6 36V12Z" fill="#14243a" stroke="${color}" stroke-opacity=".65"/><g transform="translate(6 6) scale(.75)" fill="none" stroke="${color}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">${art}</g></svg>`,
)}`;
export const builtInMarkers = [...MARKERS, ...LEGACY_MARKERS];
export const emptyIcon = builtInMarkers[0].icon;
export const soundIcon = markerIcon('<path d="M20 32V15l15-4v16M20 16l15-4"/><ellipse cx="15" cy="33" rx="5" ry="4"/><ellipse cx="30" cy="28" rx="5" ry="4"/>', "#e8b6ff");

export function readPreferences(value: StoredPreferences): Preferences {
  const source = value.custom ?? value.emotes;
  const custom = Array.isArray(source) ? source.filter(item =>
    item && typeof item.id === "string" && typeof item.name === "string" &&
    (typeof item.still === "string" || typeof item.sound === "string")) : [];
  const customIds = new Set(custom.map(item => item.id));
  const valid = new Set([...builtInMarkers.map(item => item.id), ...customIds]);
  const used = new Set<string>();
  const assigned = defaultWheel.map((_, i) => {
    const prior = value.wheel?.[i];
    const legacy = value.emoteWheel?.[i];
    const marker = prior && valid.has(prior) ? prior : legacy && customIds.has(legacy) ? legacy : undefined;
    if (!marker || used.has(marker)) return undefined;
    used.add(marker);
    return marker;
  });
  const wheel = assigned.map((marker, index) => {
    if (marker) return marker;
    const fallback = defaultWheel[index];
    const replacement = used.has(fallback) ? defaultWheel.find(id => !used.has(id))! : fallback;
    used.add(replacement);
    return replacement;
  });
  const center = value.clickMarker ?? (value.emoteClick ? value.clickEmoteId : undefined);
  const number = (raw: unknown, fallback: number, min: number, max: number) =>
    typeof raw === "number" && Number.isFinite(raw) ? Math.max(min, Math.min(max, raw)) : fallback;
  const candidate = value.triggerVk ?? 0;
  const customTrigger = value.trigger === "vk" && Number.isInteger(candidate) &&
    candidate >= 1 && candidate <= 254 && !UNSAFE_TRIGGER_VKS.includes(candidate);
  const toggle = value.toggleHotkey;
  const validToggle = toggle && Number.isInteger(toggle.mods) && toggle.mods >= 1 && toggle.mods <= 15 &&
    (toggle.mods & HOTKEY_PRIMARY_MODS) !== 0 && Number.isInteger(toggle.vk) && toggle.vk >= 1 && toggle.vk <= 254;
  return {
    enabled: value.enabled !== false,
    trigger: customTrigger ? "vk" : value.trigger && Object.hasOwn(triggers, value.trigger) ? value.trigger : defaultPreferences.trigger,
    triggerVk: value.trigger === "logi-f13" ? (candidate >= 0x70 && candidate <= 0x83 && Number.isInteger(candidate) ? candidate : 0x7c) : customTrigger ? candidate : 0,
    toggleHotkey: validToggle ? toggle : defaultToggleHotkey,
    clickPing: value.clickPing === true || value.emoteClick === true,
    dragThreshold: number(value.dragThreshold, defaultPreferences.dragThreshold, 1, 50),
    wheelSize: number(value.wheelSize, defaultPreferences.wheelSize, 300, 900),
    markerSize: number(value.markerSize ?? value.pingSize, defaultPreferences.markerSize, 35, 300),
    duration: number(value.duration, defaultPreferences.duration, 0.5, 5),
    volume: number(value.volume, defaultPreferences.volume, 0, 1),
    wheel,
    clickMarker: center && valid.has(center) ? center : defaultPreferences.clickMarker,
    custom,
  };
}

export function playMarkerSound(source: string, volume: number): void {
  if (volume === 0) return;
  const audio = new Audio(source);
  audio.volume = volume;
  void audio.play().catch(() => {});
}

const builtInSounds = new Map<number, string>();

export function playBuiltInSound(frequency: number, volume: number): void {
  if (volume === 0) return;
  let source = builtInSounds.get(frequency);
  if (!source) {
    const sampleRate = 8000;
    const frames = 880;
    const wav = new ArrayBuffer(44 + frames * 2);
    const view = new DataView(wav);
    for (const [offset, label] of [[0, "RIFF"], [8, "WAVEfmt "], [36, "data"]] as const) {
      for (let i = 0; i < label.length; i++) view.setUint8(offset + i, label.charCodeAt(i));
    }
    for (const [offset, number] of [[4, wav.byteLength - 8], [16, 16], [24, sampleRate], [28, sampleRate * 2], [40, frames * 2]]) {
      view.setUint32(offset, number, true);
    }
    for (const [offset, number] of [[20, 1], [22, 1], [32, 2], [34, 16]]) view.setUint16(offset, number, true);
    for (let i = 0; i < frames; i++) {
      const envelope = Math.min(i / 48, 1) * (1 - i / frames) ** 2;
      const sample = Math.sin(2 * Math.PI * frequency * i / sampleRate) * envelope * 8000;
      view.setInt16(44 + i * 2, sample, true);
    }
    source = URL.createObjectURL(new Blob([wav], { type: "audio/wav" }));
    builtInSounds.set(frequency, source);
  }
  playMarkerSound(source, volume);
}
