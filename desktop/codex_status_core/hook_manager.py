from __future__ import annotations

import copy
import base64
import json
import platform
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


MARKER = "--status-bridge-hook"
CODEX_MARKER = "--status-bridge-codex-notify"


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
        HookProvider("codex", "Codex", home / ".codex" / "config.toml", (("notify", "success"),), True, "官方 notify 可可靠感知每轮结束；无需安装 Skill"),
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
        marker = CODEX_MARKER if provider.key == "codex" else MARKER
        return "已启用" if marker in text else "未启用"
    except OSError:
        return "无法读取"


def install(provider: HookProvider) -> None:
    """合并官方 Hook 配置；先备份，且只增加本应用自己的条目。"""
    if not provider.supported:
        raise ValueError(provider.note)
    if provider.key == "codex":
        _install_codex_notify(provider)
        return
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
    if provider.key == "codex":
        _uninstall_codex_notify(provider)
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


def _install_codex_notify(provider: HookProvider) -> None:
    """仅替换顶层 notify 行，原回调编码保存并由分发器继续调用。"""
    import re
    path = provider.config_path
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    match = re.search(r"(?m)^notify\s*=\s*(\[[^\r\n]*\])\s*$", text)
    original: list[str] = []
    if match:
        original = json.loads(match.group(1))
    backup_path = _codex_backup_path()
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path.write_text(json.dumps({"notify": original}, ensure_ascii=False, indent=2), encoding="utf-8")
    encoded = base64.urlsafe_b64encode(json.dumps(original).encode("utf-8")).decode("ascii")
    executable = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False):
        command = [str(executable), CODEX_MARKER, encoded]
    else:
        main_file = Path(__file__).resolve().parents[1] / "windows_app" / "main.py"
        command = [str(executable), str(main_file), CODEX_MARKER, encoded]
    line = "notify = " + json.dumps(command, ensure_ascii=False)
    if match:
        text = text[:match.start()] + line + text[match.end():]
    else:
        text = line + "\n" + text
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _uninstall_codex_notify(provider: HookProvider) -> None:
    import re
    path = provider.config_path
    text = path.read_text(encoding="utf-8")
    backup_path = _codex_backup_path()
    original: list[str] = []
    if backup_path.exists():
        original = json.loads(backup_path.read_text(encoding="utf-8")).get("notify", [])
    replacement = "notify = " + json.dumps(original, ensure_ascii=False) if original else ""
    text = re.sub(r"(?m)^notify\s*=\s*\[[^\r\n]*\]\s*$", replacement, text, count=1)
    path.write_text(text, encoding="utf-8")
