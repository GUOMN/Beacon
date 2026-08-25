from __future__ import annotations

import copy
import json
import os
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
    codex_home = Path(os.environ.get("CODEX_HOME", home / ".codex")).expanduser()
    return (
        HookProvider("claude", "Claude Code", home / ".claude" / "settings.json", (("UserPromptSubmit", "running"), ("PermissionRequest", "waiting"), ("Stop", "success"), ("StopFailure", "failure"), ("PostToolUseFailure", "warning"))),
        HookProvider("gemini", "Gemini CLI", home / ".gemini" / "settings.json", (("BeforeAgent", "running"), ("Notification", "waiting"), ("AfterAgent", "success"))),
        HookProvider("cursor", "Cursor", home / ".cursor" / "hooks.json", (("beforeSubmitPrompt", "running"), ("stop", "success"), ("postToolUseFailure", "warning"))),
        HookProvider("copilot", "GitHub Copilot CLI", home / ".copilot" / "hooks.json", (("sessionStart", "running"), ("permissionRequest", "waiting"), ("agentStop", "success"), ("errorOccurred", "failure"))),
        HookProvider("codex", "Codex", codex_home / "hooks.json", (("SessionStart", "running"), ("UserPromptSubmit", "running"), ("PreToolUse", "running"), ("PostToolUse", "running"), ("PermissionRequest", "waiting"), ("Stop", "success"), ("SessionEnd", "success")), True, "使用 Codex 官方 Hook；不会替换 notify"),
    )


def _command(provider: HookProvider, event_name: str) -> str:
    executable = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False):
        return f'"{executable}" {MARKER} "{provider.key}" "{event_name}"'
    main_file = Path(__file__).resolve().parents[1] / "tauri_bridge.py"
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
    if provider.key == "codex":
        _restore_legacy_codex_notify(provider)
        _enable_codex_hooks_feature(provider)
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
        if provider.key in {"claude", "gemini", "codex"}:
            existing = next((item for item in entries if MARKER in json.dumps(item)), None)
            if existing is not None:
                # Permission hooks may stay open while the user decides.  Codex
                # otherwise treats the default short hook timeout as a failed
                # request, so repair installations made by earlier versions.
                if provider.key == "codex" and event_name == "PermissionRequest":
                    for hook in existing.get("hooks", []):
                        if isinstance(hook, dict) and MARKER in str(hook.get("command", "")):
                            hook["timeout"] = 86_400
                continue
            hook = {"type": "command", "command": command}
            if provider.key == "claude":
                hook["async"] = True
            entry = {"hooks": [hook]}
            if provider.key == "codex" and event_name == "PermissionRequest":
                hook["timeout"] = 86_400
            entries.append(entry)
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


def _codex_backup_path() -> Path:
    from .event_store import app_data_directory
    return app_data_directory() / "codex-notify-backup.json"


def _codex_config_path(provider: HookProvider) -> Path:
    return provider.config_path.parent / "config.toml"


def _restore_legacy_codex_notify(provider: HookProvider) -> None:
    """迁移旧版本 Beacon 的 notify 包装，只恢复 Beacon 曾替换的那一行。"""
    import re
    path = _codex_config_path(provider)
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    if "--status-bridge-codex-notify" not in text:
        return
    backup_path = _codex_backup_path()
    if not backup_path.exists():
        # Never delete a legacy wrapper unless its original callback can be
        # restored. The user can still remove it manually after reviewing it.
        return
    original: list[str] = []
    original = json.loads(backup_path.read_text(encoding="utf-8")).get("notify", [])
    replacement = "notify = " + json.dumps(original, ensure_ascii=False) if original else ""
    text = re.sub(r"(?m)^notify\s*=\s*\[[^\r\n]*\]\s*$", replacement, text, count=1)
    path.write_text(text, encoding="utf-8")


def _enable_codex_hooks_feature(provider: HookProvider) -> None:
    """仅启用 Codex 官方 Hook 特性，保留 config.toml 的其他内容。"""
    import re
    path = _codex_config_path(provider)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if re.search(r"(?m)^\s*hooks\s*=\s*true\s*$", text):
        return
    if re.search(r"(?m)^\s*hooks\s*=\s*false\s*$", text):
        text = re.sub(r"(?m)^\s*hooks\s*=\s*false\s*$", "hooks = true", text, count=1)
    elif re.search(r"(?m)^\[features\]\s*$", text):
        text = re.sub(r"(?m)^(\[features\]\s*)$", r"\1\nhooks = true", text, count=1)
    else:
        text = text.rstrip() + "\n\n[features]\nhooks = true\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
