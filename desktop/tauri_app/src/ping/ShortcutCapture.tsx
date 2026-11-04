import { useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { evaluateKeyDown } from "./keyCapture";
import { UNSAFE_TRIGGER_VKS, type Hotkey, type Platform } from "./shared/keys";

const hints = {
  hintUnusable: "这个键无法作为全局快捷键",
  hintNotTrigger: "请选择不会影响常用输入操作的键",
  hintNeedModifier: "请加上 Control、Alt / Option 或 Command / Win",
};

export function ShortcutCapture({ label, platform, combination, onCapture }: {
  label: string;
  platform: Platform;
  combination: boolean;
  onCapture: (hotkey: Hotkey) => void;
}) {
  const [active, setActive] = useState(false);
  const [hint, setHint] = useState("");
  const session = useRef(0);
  const capture = useRef<(Hotkey & { code: string }) | null>(null);
  const held = useRef(false);

  const finish = () => {
    session.current++;
    capture.current = null;
    held.current = false;
    setActive(false);
    setHint("");
    void invoke("suspend_ping_input", { on: false }).catch(console.error);
  };
  useEffect(() => () => {
    session.current++;
    if (held.current) void invoke("suspend_ping_input", { on: false }).catch(console.error);
  }, []);

  return <button type="button" onClick={async event => {
    if (held.current) return;
    event.currentTarget.focus();
    const id = ++session.current;
    held.current = true;
    try {
      await invoke("suspend_ping_input", { on: true });
      if (session.current === id) setActive(true);
    } catch (error) {
      if (session.current === id) {
        held.current = false;
        setHint(String(error));
      }
    }
  }} onBlur={() => { if (held.current) finish(); }}
    onKeyDown={event => {
      if (!held.current) return;
      event.preventDefault();
      event.stopPropagation();
      if (!active) return;
      const outcome = evaluateKeyDown(event, {
        platform, requireModifier: combination, forbiddenVks: combination ? [] : UNSAFE_TRIGGER_VKS,
      });
      if (outcome.kind === "cancel") finish();
      if (outcome.kind === "hint") setHint(hints[outcome.hint]);
      if (outcome.kind === "capture") {
        if (!combination && (event.ctrlKey || event.altKey || event.shiftKey || event.metaKey)) {
          setHint("轮盘使用单键，请松开其他按键");
          return;
        }
        capture.current = { vk: outcome.vk, mods: outcome.mods, code: event.code };
        setHint("松开按键确认");
      }
    }}
    onKeyUp={event => {
      if (!active) return;
      event.preventDefault();
      const value = capture.current;
      if (value?.code !== event.code) return;
      finish();
      onCapture({ mods: value.mods, vk: value.vk });
    }}>
    {active ? hint || "按键录入 · Esc 取消" : hint || label}
  </button>;
}
