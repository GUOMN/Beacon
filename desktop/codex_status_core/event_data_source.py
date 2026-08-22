from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable

from .event_store import BridgeSnapshot, StatusEventStore


class EventDataSource:
    """轮询应用私有事件库，只在快照发生变化时通知界面。"""

    def __init__(
        self,
        store: StatusEventStore,
        callback: Callable[[BridgeSnapshot], None],
        status_callback: Callable[[str], None],
        task_limit: Callable[[], int],
    ) -> None:
        self._store = store
        self._callback = callback
        self._status_callback = status_callback
        self._task_limit = task_limit
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._last_signature: tuple[object, ...] | None = None

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        self._status_callback("任务事件服务已启动")
        while not self._stop.is_set():
            try:
                snapshot = self._store.snapshot(max(1, self._task_limit()))
                signature = (
                    snapshot.busy_percent,
                    *((task.title, int(task.state), task.progress) for task in snapshot.tasks),
                )
                if signature != self._last_signature:
                    self._last_signature = signature
                    self._callback(snapshot)
            except (OSError, sqlite3.Error) as exc:
                self._status_callback(f"任务事件库读取失败：{exc}")
            self._stop.wait(1)
