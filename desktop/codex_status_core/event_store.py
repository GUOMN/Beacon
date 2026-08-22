from __future__ import annotations

import json
import os
import platform
import sqlite3
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .models import TaskSlot, TaskState


STATE_NAMES = {
    "idle": TaskState.IDLE,
    "running": TaskState.RUNNING,
    "waiting": TaskState.WAITING,
    "success": TaskState.SUCCESS,
    "warning": TaskState.WARNING,
    "failure": TaskState.FAILURE,
}


def app_data_directory() -> Path:
    """返回 Windows 与 macOS 各自规范的应用数据目录。"""
    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Application Support" / "CodexStatusBridge"
    return Path(os.getenv("APPDATA", str(Path.home()))) / "CodexStatusBridge"


@dataclass(slots=True)
class BridgeSnapshot:
    tasks: list[TaskSlot]
    busy_percent: int


class StatusEventStore:
    """上位机私有 SQLite；外部上报器不直接接触数据库文件。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or app_data_directory() / "status-events.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=2)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as database:
            database.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS task_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    state TEXT NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0,
                    source TEXT NOT NULL DEFAULT 'codex',
                    summary TEXT NOT NULL DEFAULT '',
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    occurred_at_ms INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_task_events_task_time
                    ON task_events(task_id, occurred_at_ms DESC);
                CREATE TABLE IF NOT EXISTS task_layout (
                    task_id TEXT PRIMARY KEY,
                    display_order INTEGER NOT NULL,
                    hidden INTEGER NOT NULL DEFAULT 0,
                    pinned INTEGER NOT NULL DEFAULT 0
                );
                """
            )
            columns = {row[1] for row in database.execute("PRAGMA table_info(task_events)")}
            for name, definition in (
                ("summary", "TEXT NOT NULL DEFAULT ''"),
                ("input_tokens", "INTEGER NOT NULL DEFAULT 0"),
                ("output_tokens", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if name not in columns:
                    database.execute(f"ALTER TABLE task_events ADD COLUMN {name} {definition}")
            layout_columns = {row[1] for row in database.execute("PRAGMA table_info(task_layout)")}
            if "pinned" not in layout_columns:
                database.execute("ALTER TABLE task_layout ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")

    def record(self, event: dict[str, Any]) -> None:
        state = str(event.get("state", "")).lower()
        if state not in STATE_NAMES:
            raise ValueError("unknown task state")
        task_id = str(event.get("task_id", "")).strip()[:160]
        if not task_id:
            raise ValueError("task_id is required")
        title_value = event.get("title") if "title" in event else None
        progress = max(0, min(100, int(event.get("progress", 0))))
        source = str(event.get("source") or "codex").strip()[:80]
        summary_value = event.get("summary") if "summary" in event else None
        input_tokens = max(0, int(event.get("input_tokens", 0) or 0))
        output_tokens = max(0, int(event.get("output_tokens", 0) or 0))
        occurred_at_ms = int(event.get("occurred_at_ms") or time.time_ns() // 1_000_000)
        with self._connect() as database:
            previous = database.execute(
                "SELECT title,summary FROM task_events WHERE task_id=? ORDER BY occurred_at_ms DESC,id DESC LIMIT 1",
                (task_id,),
            ).fetchone()
            title = str(title_value if title_value is not None else (previous["title"] if previous else "Agent 任务")).strip()[:240]
            summary = str(summary_value if summary_value is not None else (previous["summary"] if previous else "")).strip()[:500]
            database.execute(
                """INSERT OR IGNORE INTO task_layout(task_id, display_order, hidden)
                   VALUES (?, COALESCE((SELECT MAX(display_order) + 1 FROM task_layout), 0), 0)""",
                (task_id,),
            )
            database.execute(
                """
                INSERT INTO task_events(task_id, title, state, progress, source, summary, input_tokens, output_tokens, occurred_at_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (task_id, title, state, progress, source, summary, input_tokens, output_tokens, occurred_at_ms),
            )

    def latest_records(self, limit: int = 500) -> list[dict[str, Any]]:
        """返回每个任务的最新记录，供状态页展示和人工修正。"""
        with self._connect() as database:
            rows = database.execute(
                """
                WITH ranked AS (
                    SELECT *, ROW_NUMBER() OVER (PARTITION BY task_id ORDER BY occurred_at_ms DESC, id DESC) position
                    FROM task_events
                )
                SELECT ranked.task_id,title,state,progress,source,summary,occurred_at_ms,
                       COALESCE(layout.display_order,2147483647) display_order,
                       COALESCE(layout.pinned,0) pinned,
                       (SELECT MIN(first_event.occurred_at_ms) FROM task_events first_event WHERE first_event.task_id=ranked.task_id) first_at
                FROM ranked
                LEFT JOIN task_layout layout ON layout.task_id=ranked.task_id
                WHERE position=1 AND COALESCE(layout.hidden,0)=0
                LIMIT 1000
                """
            ).fetchall()
        records = [dict(row) for row in rows]
        active_states = {"running", "waiting", "failure", "warning"}
        pinned = sorted((item for item in records if item["pinned"]), key=lambda item: item["display_order"])
        others = sorted(
            (item for item in records if not item["pinned"]),
            key=lambda item: (
                0 if item["state"] in active_states else 1,
                item["first_at"] if item["state"] in active_states else -item["occurred_at_ms"],
            ),
        )
        ordered: list[dict[str, Any] | None] = [None] * len(records)
        overflow: list[dict[str, Any]] = []
        for item in pinned:
            position = max(0, min(len(ordered) - 1, int(item["display_order"])))
            if ordered[position] is None:
                ordered[position] = item
            else:
                overflow.append(item)
        fill = iter([*others, *overflow])
        return [next(fill) if item is None else item for item in ordered][:max(1, min(1000, limit))]

    def reorder_tasks(self, task_ids: list[str]) -> None:
        with self._connect() as database:
            for order, task_id in enumerate(task_ids):
                database.execute(
                    """INSERT INTO task_layout(task_id,display_order,hidden) VALUES(?,?,0)
                       ON CONFLICT(task_id) DO UPDATE SET display_order=excluded.display_order""",
                    (task_id, order),
                )

    def set_pinned(self, task_id: str, pinned: bool) -> None:
        with self._connect() as database:
            database.execute(
                """INSERT INTO task_layout(task_id,display_order,hidden,pinned)
                   VALUES (?,COALESCE((SELECT MAX(display_order)+1 FROM task_layout),0),0,?)
                   ON CONFLICT(task_id) DO UPDATE SET pinned=excluded.pinned""",
                (task_id, 1 if pinned else 0),
            )

    def delete_tasks(self, task_ids: list[str]) -> None:
        """物理删除任务及全部历史事件；调用方应明确提示不可恢复。"""
        with self._connect() as database:
            database.executemany("DELETE FROM task_events WHERE task_id=?", [(task_id,) for task_id in task_ids])
            database.executemany("DELETE FROM task_layout WHERE task_id=?", [(task_id,) for task_id in task_ids])

    def usage_totals(self) -> tuple[int, int]:
        now_ms = time.time_ns() // 1_000_000
        with self._connect() as database:
            def total(hours: int) -> int:
                row = database.execute(
                    "SELECT COALESCE(SUM(input_tokens + output_tokens),0) FROM task_events WHERE occurred_at_ms>=?",
                    (now_ms - hours * 60 * 60 * 1000,),
                ).fetchone()
                return int(row[0])
            return total(5), total(24 * 7)

    def snapshot(self, task_limit: int) -> BridgeSnapshot:
        now_ms = time.time_ns() // 1_000_000
        window_start = now_ms - 5 * 60 * 60 * 1000
        latest = self.latest_records(max(1, min(63, task_limit)))
        with self._connect() as database:
            recent_count = database.execute(
                "SELECT COUNT(*) FROM task_events WHERE occurred_at_ms >= ?",
                (window_start,),
            ).fetchone()[0]
            running_count = database.execute(
                """
                WITH ranked AS (
                    SELECT state, ROW_NUMBER() OVER (
                        PARTITION BY task_id ORDER BY occurred_at_ms DESC, id DESC
                    ) AS position
                    FROM task_events
                )
                SELECT COUNT(*) FROM ranked WHERE position = 1 AND state = 'running'
                """
            ).fetchone()[0]

        tasks = [
            TaskSlot(
                title=str(row["title"]),
                state=STATE_NAMES[str(row["state"])],
                progress=int(row["progress"]),
            )
            for row in latest
        ]
        while len(tasks) < task_limit:
            tasks.append(TaskSlot(title=f"任务 {len(tasks) + 1}", state=TaskState.IDLE))
        busy_percent = min(100, int(recent_count) * 4 + int(running_count) * 18)
        return BridgeSnapshot(tasks=tasks, busy_percent=busy_percent)


class EventIngestServer:
    """本地事件入口；远程部署时可由反向代理负责 TLS 与访问控制。"""

    def __init__(self, store: StatusEventStore, host: str = "127.0.0.1", port: int = 8765) -> None:
        self._store = store
        self._host = host
        self._port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        store = self._store
        expected_token = os.getenv("CODEX_STATUS_TOKEN", "")

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                if self.path != "/v1/events":
                    self.send_error(404)
                    return
                if expected_token and self.headers.get("Authorization") != f"Bearer {expected_token}":
                    self.send_error(401)
                    return
                try:
                    length = min(16_384, int(self.headers.get("Content-Length", "0")))
                    event = json.loads(self.rfile.read(length))
                    store.record(event)
                except (ValueError, TypeError, json.JSONDecodeError):
                    self.send_error(400)
                    return
                self.send_response(202)
                self.end_headers()

            def log_message(self, _format: str, *args: object) -> None:
                return

        self._server = ThreadingHTTPServer((self._host, self._port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
