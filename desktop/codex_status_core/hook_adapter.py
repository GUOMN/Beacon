from __future__ import annotations

import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any


EVENT_STATES = {
    "claude": {"UserPromptSubmit": "running", "PermissionRequest": "waiting", "Stop": "success", "StopFailure": "failure", "PostToolUseFailure": "warning"},
    "gemini": {"BeforeAgent": "running", "Notification": "waiting", "AfterAgent": "success"},
    "cursor": {"beforeSubmitPrompt": "running", "stop": "success", "postToolUseFailure": "warning"},
    "copilot": {"sessionStart": "running", "permissionRequest": "waiting", "agentStop": "success", "errorOccurred": "failure"},
    "codex": {"SessionStart": "running", "UserPromptSubmit": "running", "PreToolUse": "running", "PostToolUse": "running", "PermissionRequest": "waiting", "Stop": "success", "SessionEnd": "success"},
}


def _read_payload() -> dict[str, Any]:
    """Hook 的标准输入可能包含用户正文；这里只提取会话标识，不落盘正文。"""
    try:
        raw = sys.stdin.buffer.read(262_144)
        value = json.loads(raw or b"{}")
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _task_id(provider: str, payload: dict[str, Any]) -> str:
    for key in ("session_id", "sessionId", "conversation_id", "conversationId", "transcript_id"):
        value = payload.get(key)
        if value:
            return f"{provider}:{value}"
    seed = f"{provider}\0{Path.cwd().resolve()}".encode("utf-8", errors="replace")
    return f"{provider}:{hashlib.sha256(seed).hexdigest()[:24]}"


def _task_identity(provider: str, payload: dict[str, Any], state: str) -> dict[str, str]:
    """只在开始事件提取短标题，后续状态事件不覆盖它。"""
    if state != "running":
        return {}
    text = ""
    for key in ("prompt", "user_prompt", "message", "query", "input"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            text = " ".join(value.split())
            break
    if text:
        return {"title": text[:80]}
    return {"title": f"{provider.title()} 任务"}


def report_hook(provider: str, event_name: str, endpoint: str = "http://127.0.0.1:8765/v1/events") -> int:
    """尽力上报并永远成功退出，不能阻断宿主工具的任务。"""
    payload = _read_payload()
    state = EVENT_STATES.get(provider, {}).get(event_name)
    if not state:
        return 0
    event = {
        "task_id": _task_id(provider, payload),
        "state": state,
        "progress": 100 if state == "success" else 0,
        "source": provider,
        "occurred_at_ms": time.time_ns() // 1_000_000,
    }
    event.update(_task_identity(provider, payload, state))
    try:
        request = urllib.request.Request(endpoint, json.dumps(event).encode("utf-8"), {"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=0.2):
            pass
    except Exception:
        pass
    return 0


def report_codex_notification(raw_payload: str) -> int:
    """处理 Codex 官方 notify 的每轮结束通知，不保存输入或输出正文。"""
    try:
        payload = json.loads(raw_payload)
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}
    task_id = ""
    for key in ("thread-id", "thread_id", "conversation-id", "conversation_id"):
        if payload.get(key):
            task_id = f"codex:{payload[key]}"
            break
    if not task_id:
        task_id = _task_id("codex", payload)
    event = {
        "task_id": task_id,
        "state": "success",
        "progress": 100,
        "source": "codex",
        "occurred_at_ms": time.time_ns() // 1_000_000,
    }
    try:
        request = urllib.request.Request("http://127.0.0.1:8765/v1/events", json.dumps(event).encode("utf-8"), {"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=0.2):
            pass
    except Exception:
        pass
    return 0
