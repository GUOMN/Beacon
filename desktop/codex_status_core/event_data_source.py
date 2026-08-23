from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import replace

import psutil

from .event_store import BridgeSnapshot, StatusEventStore


def weighted_busy_percent(
    task: float,
    cpu: float,
    memory: float,
    disk: float,
    network: float,
    token: float = 0,
    weights: tuple[float, float, float, float, float, float] = (30, 20, 20, 10, 10, 10),
) -> int:
    """合并任务与系统资源负载；各输入均为 0~100。"""
    values = [max(0.0, min(100.0, value)) for value in (task, token, cpu, memory, disk, network)]
    safe_weights = [max(0.0, value) for value in weights]
    total = sum(safe_weights)
    if total <= 0:
        return 0
    return round(sum(value * weight for value, weight in zip(values, safe_weights)) / total)


class SystemLoadSampler:
    """跨 Windows/macOS 采样 CPU、内存、磁盘活动与网络吞吐。"""

    NETWORK_FULL_BYTES_PER_SECOND = 10 * 1024 * 1024

    def __init__(self) -> None:
        self._last_time = time.monotonic()
        self._last_disk_busy_ms = self._disk_busy_ms()
        network = psutil.net_io_counters()
        self._last_network_bytes = int(network.bytes_sent + network.bytes_recv)
        psutil.cpu_percent(interval=None)

    @staticmethod
    def _disk_busy_ms() -> int:
        counters = psutil.disk_io_counters(perdisk=True) or {}
        return sum(int(getattr(item, "busy_time", 0) or 0) for item in counters.values())

    def sample(self) -> tuple[float, float, float, float]:
        now = time.monotonic()
        elapsed = max(0.1, now - self._last_time)
        disk_busy_ms = self._disk_busy_ms()
        network = psutil.net_io_counters()
        network_bytes = int(network.bytes_sent + network.bytes_recv)
        disk_percent = min(100.0, max(0.0, disk_busy_ms - self._last_disk_busy_ms) / (elapsed * 10.0))
        network_percent = min(
            100.0,
            max(0.0, network_bytes - self._last_network_bytes)
            / elapsed
            / self.NETWORK_FULL_BYTES_PER_SECOND
            * 100.0,
        )
        self._last_time = now
        self._last_disk_busy_ms = disk_busy_ms
        self._last_network_bytes = network_bytes
        return psutil.cpu_percent(interval=None), psutil.virtual_memory().percent, disk_percent, network_percent


class EventDataSource:
    """轮询应用私有事件库，只在快照发生变化时通知界面。"""

    def __init__(
        self,
        store: StatusEventStore,
        callback: Callable[[BridgeSnapshot], None],
        status_callback: Callable[[str], None],
        task_limit: Callable[[], int],
        busy_weights: Callable[[], tuple[float, float, float, float, float, float]],
    ) -> None:
        self._store = store
        self._callback = callback
        self._status_callback = status_callback
        self._task_limit = task_limit
        self._busy_weights = busy_weights
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._last_signature: tuple[object, ...] | None = None
        self._system_load = SystemLoadSampler()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        self._status_callback("任务事件服务已启动")
        while not self._stop.is_set():
            try:
                snapshot = self._store.snapshot(max(1, self._task_limit()))
                cpu, memory, disk, network = self._system_load.sample()
                snapshot = replace(
                    snapshot,
                    busy_percent=weighted_busy_percent(
                        snapshot.task_load_percent, cpu, memory, disk, network,
                        snapshot.token_load_percent,
                        self._busy_weights(),
                    ),
                    cpu_available_percent=round(100 - cpu),
                    memory_available_percent=round(100 - memory),
                    disk_available_percent=round(100 - disk),
                )
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
