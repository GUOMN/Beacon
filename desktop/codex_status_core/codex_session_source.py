from __future__ import annotations

import glob
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .event_store import StatusEventStore


THREAD_ID_PATTERN = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")
FAILED_TOOL_STATUSES = {"error", "failed", "failure", "denied", "rejected", "cancelled", "canceled"}


def _tool_output_failed(value: Any) -> bool:
    """Return whether a completed Codex tool call reports an error or rejection."""
    if isinstance(value, dict):
        for key in ("is_error", "isError"):
            if value.get(key) is True:
                return True
        for key in ("exit_code", "exitCode"):
            code = value.get(key)
            if isinstance(code, int) and code != 0:
                return True
        status = value.get("status")
        if isinstance(status, str) and status.lower() in FAILED_TOOL_STATUSES:
            return True
        return any(_tool_output_failed(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_tool_output_failed(item) for item in value)
    if isinstance(value, str):
        return bool(re.search(r"\b(?:approval\s+)?(?:denied|rejected|cancelled|canceled)\b|\b(?:tool|command|script|execution)\s+failed\b", value, re.IGNORECASE))
    return False


class CodexSessionSource:
    """只读跟踪 Codex 会话事件流，不写入 Codex 文件。"""

    def __init__(self, store: StatusEventStore, status_callback: Callable[[str], None]) -> None:
        self._store = store
        self._status_callback = status_callback
        self._root = Path.home() / ".codex"
        self._offsets: dict[Path, int] = {}
        self._titles: dict[str, str] = {}
        self._waiting_calls: dict[str, set[str]] = {}
        self._tool_calls: dict[str, set[str]] = {}
        self._failed_threads: set[str] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._load_titles()
        for path in self._session_files():
            # Existing Codex history is not an application data source. Start at
            # EOF and ingest only events produced while this client is running.
            try:
                stat = path.stat()
            except FileNotFoundError:
                # Codex 可能在 glob 完成后立刻归档或轮换会话文件。
                continue
            self._offsets[path] = stat.st_size
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)
        self._thread = None

    def _run(self) -> None:
        while not self._stop.wait(0.5):
            try:
                self.poll_once()
            except Exception:
                continue

    def _session_files(self) -> list[Path]:
        return [Path(value) for value in glob.glob(str(self._root / "sessions" / "**" / "*.jsonl"), recursive=True)]

    def _load_titles(self) -> None:
        path = self._root / "session_index.jsonl"
        if not path.exists():
            return
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                item = json.loads(line)
                if item.get("id") and item.get("thread_name"):
                    self._titles[str(item["id"])] = str(item["thread_name"]).strip()[:120]
        except Exception:
            pass

    @staticmethod
    def _thread_id(path: Path) -> str:
        matches = THREAD_ID_PATTERN.findall(path.stem)
        return matches[-1] if matches else path.stem

    def poll_once(self) -> None:
        self._load_titles()
        for path in self._session_files():
            # 新创建的会话文件必须从头读取，否则会漏掉最前面的 task_started。
            offset = self._offsets.get(path, 0)
            try:
                size = path.stat().st_size
            except FileNotFoundError:
                self._offsets.pop(path, None)
                continue
            if size < offset:
                offset = 0
            if size == offset:
                self._offsets[path] = offset
                continue
            try:
                with path.open("rb") as stream:
                    stream.seek(offset)
                    while True:
                        line_offset = stream.tell()
                        line = stream.readline()
                        if not line:
                            break
                        try:
                            event_key = f"{path.resolve()}:{line_offset}"
                            self._consume(self._thread_id(path), json.loads(line), event_key)
                        except (json.JSONDecodeError, ValueError, TypeError):
                            continue
                    self._offsets[path] = stream.tell()
            except FileNotFoundError:
                self._offsets.pop(path, None)

    def _consume(self, thread_id: str, envelope: dict[str, Any], event_key: str | None = None) -> None:
        payload = envelope.get("payload") or {}
        envelope_type = envelope.get("type")
        event_type = payload.get("type")
        task_id = f"codex:{thread_id}"
        title = self._titles.get(thread_id, "Codex 任务")

        if envelope_type == "event_msg" and event_type == "user_message":
            return
        if envelope_type == "event_msg" and event_type == "token_count":
            info = payload.get("info")
            if isinstance(info, dict):
                usage = info.get("total_token_usage") or {}
                if isinstance(usage, dict):
                    self._store.update_task_usage(
                        task_id,
                        int(usage.get("input_tokens") or 0),
                        int(usage.get("output_tokens") or 0) + int(usage.get("reasoning_output_tokens") or 0),
                        int(usage.get("total_tokens") or 0),
                        int(info.get("model_context_window") or 0),
                    )
            return
        state: str | None = None
        if envelope_type == "event_msg" and event_type == "task_started":
            self._failed_threads.discard(thread_id)
            state = "running"
        elif envelope_type == "event_msg" and event_type == "task_complete":
            state = "failure" if thread_id in self._failed_threads else "success"
        elif envelope_type == "event_msg" and event_type == "turn_aborted":
            state = "warning"
        elif envelope_type == "response_item" and event_type in {"custom_tool_call", "function_call"}:
            call_id = str(payload.get("call_id") or payload.get("id") or "")
            if call_id:
                self._tool_calls.setdefault(thread_id, set()).add(call_id)
            try:
                raw_arguments = payload.get("input") or payload.get("arguments") or "{}"
                arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
            except (json.JSONDecodeError, TypeError):
                arguments = {}
            if isinstance(arguments, dict) and (arguments.get("sandbox_permissions") == "require_escalated" or arguments.get("require_approval") is True):
                self._waiting_calls.setdefault(thread_id, set()).add(call_id)
                state = "waiting"
        elif envelope_type == "response_item" and event_type in {"custom_tool_call_output", "function_call_output"}:
            call_id = str(payload.get("call_id") or "")
            waiting = self._waiting_calls.setdefault(thread_id, set())
            known_call = call_id in self._tool_calls.setdefault(thread_id, set())
            if call_id in waiting:
                waiting.discard(call_id)
                state = "failure" if _tool_output_failed(payload) else "running"
            elif known_call and _tool_output_failed(payload):
                state = "failure"
        if state is None:
            return
        if state == "failure":
            self._failed_threads.add(thread_id)
        self._store.record({
            "task_id": task_id,
            "title": title,
            "state": state,
            "progress": 100 if state == "success" else 0,
            "source": "codex-live",
            "event_key": event_key,
        })
