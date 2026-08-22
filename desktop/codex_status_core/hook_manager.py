from __future__ import annotations

import copy
import json
import platform
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


MARKER = "--status-bridge-hook"


@dataclass(frozen=True, slots=True)
class HookProvider:
    key: str
    name: str
    config_path: Path
    events: tuple[tuple[str, str], ...]
    supported: bool = True
    note: str = ""


def providers() -> tuple[HookProvider, ...]:
    home = Path.home()
    return (
        HookProvider("claude", "Claude Code", home / ".claude" / "settings.json", (("UserPromptSubmit", "running"), ("PermissionRequest", "waiting"), ("Stop", "success"), ("StopFailure", "failure"), ("PostToolUseFailure", "warning"))),
        HookProvider("gemini", "Gemini CLI", home / ".gemini" / "settings.json", (("BeforeAgent", "running"), ("Notification", "waiting"), ("AfterAgent", "success"))),
        HookProvider("cursor", "Cursor", home / ".cursor" / "hooks.json", (("beforeSubmitPrompt", "running"), ("stop", "success"), ("postToolUseFailure", "warning"))),
        HookProvider("copilot", "GitHub Copilot CLI", home / ".copilot" / "hooks.json", (("sessionStart", "running"), ("permissionRequest", "waiting"), ("agentStop", "success"), ("errorOccurred", "failure"))),
        HookProvider("codex", "Codex", home / ".codex" / "config.toml", (), False, "公开稳定的完整 Hook 事件表尚未提供，暂不自动改配置"),
    )


def _command(provider: HookProvider, event_name: str) -> str:
    executable = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False):
        return f'"{executable}" {MARKER} "{provider.key}" "{event_name}"'
    main_file = Path(__file__).resolve().parents[1] / "windows_app" / "main.py"
    return f'"{executable}" "{main_file}" {MARKER} "{provider.key}" "{event_name}"'


def status(provider: HookProvider) -> str:
    if not provider.supported:
        return "暂不支持"
    if not provider.config_path.exists():
        return "未配置"
    try:
        text = provider.config_path.read_text(encoding="utf-8")
        return "已启用" if MARKER in text else "未启用"
    except OSError:
        return "无法读取"


def install(provider: HookProvider) -> None:
    """合并官方 Hook 配置；先备份，且只增加本应用自己的条目。"""
    if not provider.supported:
        raise ValueError(provider.note)
    path = provider.config_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
        backup = path.with_suffix(path.suffix + ".bak-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
        shutil.copy2(path, backup)
    else:
        data = {}
    hooks = data.setdefault("hooks", {})
    for event_name, _state in provider.events:
        command = _command(provider, event_name)
        entries = hooks.setdefault(event_name, [])
        # Claude/Gemini 的命令 Hook 使用嵌套 hooks；Cursor/Copilot 使用直接命令项。
        if provider.key in {"claude", "gemini"}:
            if any(MARKER in json.dumps(item) for item in entries):
                continue
            hook = {"type": "command", "command": command}
            if provider.key == "claude":
                hook["async"] = True
            entries.append({"hooks": [hook]})
        else:
            if any(MARKER in json.dumps(item) for item in entries):
                continue
            entries.append({"command": command})
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def uninstall(provider: HookProvider) -> None:
    path = provider.config_path
    if not path.exists() or not provider.supported:
        return
    data = json.loads(path.read_text(encoding="utf-8") or "{}")
    hooks = data.get("hooks", {})
    for event_name, _state in provider.events:
        entries = hooks.get(event_name, [])
        hooks[event_name] = [item for item in entries if MARKER not in json.dumps(item)]
        if not hooks[event_name]:
            hooks.pop(event_name, None)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
