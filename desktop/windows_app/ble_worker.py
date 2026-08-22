from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable

from codex_status_core.ble_client import DashboardBLEClient
from codex_status_core.models import DashboardSnapshot


class BLEWorker:
    """让 asyncio 蓝牙核心运行在后台线程，避免阻塞桌面界面。"""

    def __init__(self, status_callback: Callable[[str], None]) -> None:
        self._status_callback = status_callback
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: DashboardBLEClient | None = None
        self._thread = threading.Thread(target=self._thread_main, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def submit(self, snapshot: DashboardSnapshot) -> None:
        if self._loop is None or self._client is None:
            self._status_callback("蓝牙后台尚未就绪")
            return
        asyncio.run_coroutine_threadsafe(self._client.submit(snapshot), self._loop)

    def stop(self) -> None:
        if self._loop is not None and self._client is not None:
            future = asyncio.run_coroutine_threadsafe(self._client.stop(), self._loop)
            try:
                future.result(timeout=3)
            except Exception:
                pass

    def _thread_main(self) -> None:
        asyncio.run(self._async_main())

    async def _async_main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._client = DashboardBLEClient(self._status_callback)
        await self._client.run()
