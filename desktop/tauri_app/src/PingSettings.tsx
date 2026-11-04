import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { ShortcutCapture } from "./ping/ShortcutCapture";
import { hotkeyLabel, vkLabel } from "./ping/shared/keys";
import { WheelEditor } from "./ping/renderer/settings/WheelEditor";
import { prepareEmote, prepareSound } from "./ping/renderer/settings/emoteImport";
import { sniffImage } from "./ping/shared/imageSniff";
import { sniffAudio } from "./ping/shared/audioSniff";
import { MAX_CUSTOM_EMOTES } from "./ping/shared/emotes";
import {
  builtInMarkers, defaultPreferences, defaultWheel, playBuiltInSound,
  playMarkerSound, readPreferences, soundIcon, triggers, type CustomMarker, type Preferences,
  type StoredPreferences, type Trigger,
} from "./ping/preferences";

const dataUrl = (bytes: Uint8Array, mime: string) =>
  `data:${mime};base64,${btoa(Array.from({ length: Math.ceil(bytes.length / 4096) }, (_, i) =>
    String.fromCharCode(...bytes.subarray(i * 4096, (i + 1) * 4096))).join(""))}`;

// getRandomValues is also available in WebViews that do not expose randomUUID.
const newMarkerId = () => Array.from(crypto.getRandomValues(new Uint8Array(16)),
  byte => byte.toString(16).padStart(2, "0")).join("");

const imageImportErrors = {
  type: "请选择可解码的 PNG、JPEG、GIF 或 WebP 图片",
  size: "图片超过 8 MB 或边长超过 8192 像素",
  animSize: "动图超过 5 MB 或边长超过 1024 像素",
  detail: "缩略图压缩后仍然过大",
  full: "素材库已满",
  duplicate: "图片已存在",
  disk: "无法读取图片",
};

async function readSound(file: File) {
  const result = await prepareSound(file);
  if (!result.ok) throw new Error(`无法导入音效（${result.error}）`);
  const type = sniffAudio(result.bytes);
  return dataUrl(result.bytes, type === "mp3" ? "audio/mpeg" : `audio/${type}`);
}

export default function PingSettings() {
  const [prefs, setPrefs] = useState<Preferences>(defaultPreferences);
  const [input, setInput] = useState({ status: "" });
  const [message, setMessage] = useState("");
  const [imageMessage, setImageMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState("");
  const mac = /Mac/.test(navigator.platform);
  const platform = mac ? "mac" : "win";
  const mappedButton = prefs.trigger === "logi-f13";
  const mappedVk = prefs.triggerVk || 0x7c;
  const triggerLabel = mappedButton ? vkLabel(mappedVk, platform) : prefs.trigger === "vk" ? vkLabel(prefs.triggerVk, platform) : triggers[prefs.trigger];
  const toggleLabel = hotkeyLabel(prefs.toggleHotkey, platform);

  const refreshInput = () => invoke<typeof input>("ping_input_status").then(setInput).catch(error => setMessage(String(error)));
  useEffect(() => {
    void invoke<StoredPreferences>("ping_preferences").then(value => setPrefs(readPreferences(value))).catch(error => setMessage(String(error)));
    void refreshInput();
    const timer = window.setInterval(refreshInput, 1200);
    return () => window.clearInterval(timer);
  }, []);

  const importImages = async (files: File[]) => {
    if (!files.length) return;
    setBusy(true);
    setImageMessage("正在导入图片…");
    const custom = [...prefs.custom];
    const errors: string[] = [];
    try {
      for (const file of files) {
        if (custom.length >= MAX_CUSTOM_EMOTES) {
          errors.push(`最多可保存 ${MAX_CUSTOM_EMOTES} 个自定义素材`);
          break;
        }
        const result = await prepareEmote(file);
        if (!result.ok) {
          errors.push(`${file.name} 无法导入（${imageImportErrors[result.error]}）`);
          continue;
        }
        const { name, still, anim } = result.file;
        const stillUrl = dataUrl(still, `image/${sniffImage(still)}`);
        if (custom.some(item => item.still === stillUrl)) {
          errors.push(`${file.name} 已在素材库中`);
          continue;
        }
        custom.push({
          id: newMarkerId(), name: name.slice(0, 24), still: stillUrl,
          animation: anim ? dataUrl(anim, `image/${sniffImage(anim)}`) : undefined,
        });
      }
      setPrefs(current => ({ ...current, custom }));
      const added = custom.length - prefs.custom.length;
      setImageMessage([added ? `已导入 ${added} 张图片，点击「保存轮盘配置」应用` : "", ...errors].filter(Boolean).join("。"));
    } catch {
      setImageMessage("无法读取所选图片，请重新选择文件");
    } finally {
      setBusy(false);
    }
  };

  const importSound = async (file: File, marker?: CustomMarker) => {
    if (!marker && prefs.custom.length >= MAX_CUSTOM_EMOTES) {
      setMessage(`最多可保存 ${MAX_CUSTOM_EMOTES} 个自定义素材`);
      return;
    }
    setBusy(true);
    try {
      const sound = await readSound(file);
      setPrefs(current => ({
        ...current,
        custom: marker ? current.custom.map(item => item.id === marker.id ? { ...item, sound } : item) :
          [...current.custom, { id: newMarkerId(), name: file.name.replace(/\.[^.]+$/, "").slice(0, 24), sound }],
      }));
      setMessage("音效已导入，保存后应用");
    } catch (error) {
      setMessage(String(error));
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if ((prefs.trigger === "vk" && prefs.triggerVk === prefs.toggleHotkey.vk) || (mappedButton && mappedVk === prefs.toggleHotkey.vk)) {
      setMessage("轮盘触发键和暂停快捷键的主键需要不同");
      return;
    }
    setBusy(true);
    try {
      await invoke("save_ping_preferences", { settings: prefs });
      setMessage("已保存并应用");
    } catch (error) {
      setMessage(String(error));
    } finally {
      setBusy(false);
    }
  };

  const remove = (id: string) => setPrefs(current =>
    readPreferences({ ...current, custom: current.custom.filter(item => item.id !== id) }));
  const slider = (key: "wheelSize" | "markerSize" | "duration" | "dragThreshold" | "volume",
    label: string, min: number, max: number, step: number) =>
    <label>{label} · {prefs[key]}<input type="range" min={min} max={max} step={step} value={prefs[key]}
      onChange={e => setPrefs({ ...prefs, [key]: e.target.valueAsNumber })}/></label>;

  const pool = [
    ...builtInMarkers,
    ...prefs.custom.map(item => ({ id: item.id, name: item.name, icon: item.still ?? soundIcon, onRemove: () => remove(item.id) })),
  ];
  const status = input.status === "noAccess" ? "缺少 macOS 辅助功能权限" :
    input.status === "ready" ? "全局输入已就绪" : input.status === "failed" ? "全局输入监听已停止" : "正在等待全局输入";

  const sideButton = mappedButton || prefs.trigger === "mouse4" || prefs.trigger === "mouse5";
  return <div className="ping-settings">
    <section className="card ping-controls ping-status">
      <div><span className="ping-kicker">全局输入</span><h2>{status}</h2></div>
      {mappedButton && <p>在 Logi Options+ 的「所有应用」里，把侧键分配为键盘快捷键 {triggerLabel}，替换「后退」。如果驱动只发送短按，移动选中后点击左键确认，再按侧键可取消。</p>}
      <p>{sideButton ? "按下侧键打开轮盘，移动鼠标选择，松开侧键或点击左键确认。" : "按住触发键，用鼠标左键拖动并松开。"}{toggleLabel} 暂停或恢复。</p>
      {mac && input.status !== "ready" && <>
        <p>在「辅助功能」里启用 Beacon。如果列表里没有 Beacon，点击「＋」，从「应用程序」中选择 Beacon.app。</p>
        <button onClick={() => void invoke("open_ping_accessibility").catch(error => setMessage(String(error)))}>打开辅助功能设置</button>
      </>}
      {input.status !== "ready" && <button onClick={() => void invoke("start_ping_overlay").then(refreshInput).catch(error => setMessage(String(error)))}>重试启动</button>}
      <div className="ping-save"><span role="status">{message}</span>
        <button className="primary" disabled={busy} onClick={() => void save()}>保存轮盘配置</button></div>
    </section>
    <WheelEditor wheel={prefs.wheel} center={prefs.clickMarker} centerOn={prefs.clickPing} trigger={triggerLabel}
      pool={pool} defaults={{ wheel: defaultWheel, center: "attention" }}
      labels={{ title: "标记轮盘", desc: "拖动素材到槽位，或先选素材再点击槽位", poolTitle: "素材库", poolDesc: "内置标记、自定义图片和音效共用这一个轮盘" }}
      onDropFiles={files => { if (!busy) void importImages(files); }}
      fxIds={prefs.custom.filter(item => item.still).map(item => item.id)}
      fxFor={id => prefs.custom.find(item => item.id === id)?.fx}
      onFxPick={(id, fx) => setPrefs(current => ({ ...current, custom: current.custom.map(item => item.id === id ? { ...item, fx } : item) }))}
      onPreview={id => {
        const custom = prefs.custom.find(item => item.id === id);
        const builtIn = builtInMarkers.find(item => item.id === id);
        setPreview(custom?.animation ?? pool.find(item => item.id === id)?.icon ?? "");
        if (custom?.sound) playMarkerSound(custom.sound, prefs.volume);
        if (builtIn) playBuiltInSound(builtIn.frequency, prefs.volume);
      }}
      onChange={patch => setPrefs(current => ({ ...current, wheel: patch.wheel ?? current.wheel, clickMarker: patch.center ?? current.clickMarker }))}
      poolExtra={<div className="weImport">
        <div role="status">{imageMessage}</div>
        <label>导入图片<input type="file" accept="image/png,image/jpeg,image/gif,image/webp" multiple disabled={busy}
          onChange={event => { const files = Array.from(event.target.files ?? []); event.target.value = ""; void importImages(files); }}/></label>
        <label>导入纯音效<input type="file" accept="audio/ogg,audio/mpeg,audio/wav" disabled={busy}
          onChange={event => { const file = event.target.files?.[0]; event.target.value = ""; if (file) void importSound(file); }}/></label>
      </div>}/>
    <section className="card ping-controls">
      <div><span className="ping-kicker">快捷键与表现</span><h2>轮盘设置</h2></div>
      <div className="ping-options">
        <label><input type="checkbox" checked={prefs.enabled} onChange={e => setPrefs({ ...prefs, enabled: e.target.checked })}/>启用全局轮盘</label>
        <label>触发键<select value={prefs.trigger} onChange={e => setPrefs({ ...prefs, trigger: e.target.value as Trigger, triggerVk: e.target.value === "logi-f13" ? 0x7c : prefs.triggerVk })}>
          {Object.entries(triggers).map(([id, name]) => <option key={id} value={id}>{name}</option>)}
          {prefs.trigger === "vk" && <option value="vk">自定义 · {triggerLabel}</option>}
        </select></label>
        {mappedButton && <label>映射功能键<select value={mappedVk}
          onChange={e => setPrefs(current => ({ ...current, triggerVk: Number(e.target.value) }))}>
          {Array.from({ length: 20 }, (_, index) =>
            <option key={index} value={0x70 + index}>F{index + 1}</option>)}
        </select></label>}
        <div><ShortcutCapture label={mappedButton ? `录入映射键 · ${triggerLabel}` : "录入自定义触发键"} platform={platform} combination={false}
          onCapture={({ vk }) => {
            if (mappedButton && (vk < 0x70 || vk > 0x83)) {
              setMessage("罗技侧键映射使用 F1 至 F20");
              return;
            }
            setPrefs(current => ({ ...current, trigger: mappedButton ? "logi-f13" : "vk", triggerVk: vk }));
          }}/></div>
        <div><ShortcutCapture label={`暂停 / 恢复 · ${toggleLabel}`} platform={platform} combination
          onCapture={toggleHotkey => setPrefs(current => ({ ...current, toggleHotkey }))}/></div>
        {!sideButton && <label><input type="checkbox" checked={prefs.clickPing} onChange={e => setPrefs({ ...prefs, clickPing: e.target.checked })}/>触发键 + 点击发送中心标记</label>}
        {!sideButton && slider("dragThreshold", "拖动阈值", 1, 50, 1)}{slider("wheelSize", "轮盘尺寸", 300, 900, 10)}
        {slider("markerSize", "标记尺寸", 35, 300, 5)}{slider("duration", "标记停留秒数", 0.5, 5, 0.1)}
        {slider("volume", "音量", 0, 1, 0.05)}
      </div>
    </section>
    {prefs.custom.some(item => item.still) && <section className="card ping-controls"><h2>给图片配音效</h2>
      {prefs.custom.filter(item => item.still).map(item => <label key={item.id}>{item.name} {item.sound ? "· 已添加音效" : ""}
        <input type="file" accept="audio/ogg,audio/mpeg,audio/wav" disabled={busy}
          onChange={event => { const file = event.target.files?.[0]; event.target.value = ""; if (file) void importSound(file, item); }}/>
        {item.sound && <button onClick={() => setPrefs(current => ({ ...current, custom: current.custom.map(marker => marker.id === item.id ? { ...marker, sound: undefined } : marker) }))}>移除音效</button>}
      </label>)}
    </section>}
    {preview && <section className="card ping-controls"><h2>素材预览</h2><img className="ping-preview" src={preview} alt="所选素材"/></section>}
  </div>;
}
