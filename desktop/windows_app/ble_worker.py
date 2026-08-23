from __future__ import annotations

import asyncio
import sys
import threading
from collections.abc import Callable

from bleak import BleakClient, BleakScanner

from codex_status_core.ble_client import DashboardBLEClient
from codex_status_core.models import DashboardSnapshot
from codex_status_core.protocol import BLEProtocol


class BLEWorker:
    """让 asyncio 蓝牙核心运行在后台线程，避免阻塞桌面界面。"""

    def __init__(self, status_callback: Callable[[str], None], device_id: str) -> None:
        self._status_callback = status_callback
        self._device_id = device_id
        self._connected = threading.Event()
        self._firmware_info = "读取中"
        self._ota_progress = 0
        self._ota_state = "idle"
        self._ota_message = ""
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

    def submit_ota(self, firmware: bytes) -> None:
        if self._loop is None or self._client is None or not self.is_connected:
            self._status_callback("灯板尚未连接，不能开始蓝牙升级")
            return
        self._ota_progress = 0
        self._ota_state = "running"
        self._ota_message = "正在准备固件升级"
        future = asyncio.run_coroutine_threadsafe(self._client.submit_ota(firmware), self._loop)
        try:
            future.result(timeout=2)
        except Exception as exc:
            self._status_callback(f"提交固件升级失败：{exc}")

    def stop(self) -> None:
        if self._loop is not None and self._client is not None:
            future = asyncio.run_coroutine_threadsafe(self._client.stop(), self._loop)
            try:
                future.result(timeout=3)
            except Exception:
                pass

    @property
    def is_connected(self) -> bool:
        """供界面只读查询当前蓝牙连接状态。"""
        return self._connected.is_set()

    @property
    def firmware_info(self) -> str:
        return self._firmware_info

    @property
    def ota_status(self) -> dict[str, object]:
        return {"state": self._ota_state, "progress": self._ota_progress, "message": self._ota_message}

    def _handle_status(self, message: str) -> None:
        if message == "蓝牙已连接":
            self._connected.set()
        elif message == "蓝牙已断开":
            self._connected.clear()
        elif message.startswith("固件信息："):
            self._firmware_info = message.removeprefix("固件信息：")
        elif message.startswith("蓝牙升级写入 ") and message.endswith("%"):
            try:
                self._ota_progress = int(message.removeprefix("蓝牙升级写入 ").removesuffix("%"))
            except ValueError:
                pass
            self._ota_state = "running"
            self._ota_message = message
        elif message.startswith("固件校验通过"):
            self._ota_progress = 100
            self._ota_state = "success"
            self._ota_message = message
        elif message.startswith("蓝牙异常：") and self._ota_state == "running":
            self._ota_state = "error"
            self._ota_message = message
        self._status_callback(message)

    def _thread_main(self) -> None:
        asyncio.run(self._async_main())

    async def _async_main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._client = DashboardBLEClient(self._handle_status, self._device_id)
        await self._client.run()


def scan_status_devices(result_callback: Callable[[list[dict[str, object]]], None],
                        status_callback: Callable[[str], None]) -> None:
    """在独立线程扫描所有状态灯板，避免阻塞 Tk 主线程。"""
    async def scan() -> None:
        status_callback("正在扫描附近灯板")
        # macOS 首次使用蓝牙时会在 discover 过程中弹权限确认。CoreBluetooth
        # 会立即中断这一轮扫描，即使用户随后点了允许，因此授权后自动重试。
        attempts = 3 if sys.platform == "darwin" else 1
        discovered = {}
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                discovered = await BleakScanner.discover(timeout=5, return_adv=True)
                last_error = None
                if discovered or sys.platform != "darwin":
                    break
            except Exception as exc:
                last_error = exc
                if sys.platform != "darwin" or attempt == attempts - 1:
                    raise
            if attempt < attempts - 1:
                status_callback("蓝牙权限已请求，正在自动重试扫描")
                await asyncio.sleep(2.5)
        if last_error is not None:
            raise last_error
        devices: list[dict[str, object]] = []
        for device, advertisement in discovered.values():
            name = advertisement.local_name or device.name
            device_id = BLEProtocol.device_id_from_name(name)
            if device_id is not None:
                devices.append({
                    "name": name or "Codex 灯板",
                    "device_id": device_id,
                    "address": device.address,
                    "rssi": advertisement.rssi,
                })
        devices.sort(key=lambda item: int(item["rssi"]), reverse=True)
        result_callback(devices)
        status_callback(f"扫描完成，发现 {len(devices)} 块灯板")

    def runner() -> None:
        try:
            asyncio.run(scan())
        except Exception as exc:
            status_callback(f"扫描失败：{exc}")
            result_callback([])

    threading.Thread(target=runner, daemon=True).start()


def identify_status_device(address: str, status_callback: Callable[[str], None]) -> None:
    """连接选中设备并触发三秒白色识别动画。"""
    async def identify() -> None:
        status_callback("正在连接选中灯板进行识别")
        async with BleakClient(address) as client:
            await client.write_gatt_char(
                BLEProtocol.CONTROL_UUID,
                BLEProtocol.encode_identify(0),
                response=True,
            )
        status_callback("识别命令已发送，请观察白色流水灯板")

    def runner() -> None:
        try:
            asyncio.run(identify())
        except Exception as exc:
            status_callback(f"识别失败：{exc}")

    threading.Thread(target=runner, daemon=True).start()
