from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress

from bleak import BleakClient, BleakScanner

from .models import DashboardSnapshot
from .protocol import BLEProtocol


StatusCallback = Callable[[str], None]


class DashboardBLEClient:
    """跨 Windows/macOS 的蓝牙连接、自动重连和心跳核心。"""

    def __init__(self, status_callback: StatusCallback, device_id: str | None = None) -> None:
        self._status_callback = status_callback
        self._stop_event = asyncio.Event()
        self._outgoing: asyncio.Queue[DashboardSnapshot] = asyncio.Queue(maxsize=1)
        self._ota_outgoing: asyncio.Queue[bytes] = asyncio.Queue(maxsize=1)
        self._write_lock = asyncio.Lock()
        self._sequence = 0
        self._connected_client: BleakClient | None = None
        self._device_id = device_id.upper() if device_id else None

    async def run(self) -> None:
        self._stop_event.clear()
        while not self._stop_event.is_set():
            try:
                await self._connect_and_serve()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # 蓝牙异常不能结束后台线程，应继续重连。
                self._status_callback(f"蓝牙异常：{exc}")
            if not self._stop_event.is_set():
                self._status_callback("两秒后重新搜索灯板")
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._stop_event.wait(), timeout=2)

    async def stop(self) -> None:
        self._stop_event.set()
        if self._connected_client is not None:
            with suppress(Exception):
                await self._connected_client.disconnect()

    async def submit(self, snapshot: DashboardSnapshot) -> None:
        # 队列只保留最新状态，避免断连后发送过时的中间状态。
        if self._outgoing.full():
            with suppress(asyncio.QueueEmpty):
                self._outgoing.get_nowait()
        await self._outgoing.put(snapshot)

    async def submit_ota(self, firmware: bytes) -> None:
        if self._ota_outgoing.full():
            raise RuntimeError("已有固件升级正在等待")
        await self._ota_outgoing.put(firmware)

    async def _connect_and_serve(self) -> None:
        target = self._device_id or "未绑定的六灯面板"
        self._status_callback(f"正在搜索 {target}")
        device = await BleakScanner.find_device_by_filter(
            lambda found, advertisement: (
                (lambda found_id: found_id is not None and
                 (self._device_id is None or found_id == self._device_id))(
                    BLEProtocol.device_id_from_name(advertisement.local_name or found.name)
                )
            ),
            timeout=10,
        )
        if device is None:
            raise RuntimeError("没有发现六灯面板")

        disconnected = asyncio.Event()

        def on_disconnect(_: BleakClient) -> None:
            disconnected.set()

        async with BleakClient(device, disconnected_callback=on_disconnect) as client:
            self._connected_client = client
            self._status_callback("蓝牙已连接")
            writer = asyncio.create_task(self._writer_loop(client))
            heartbeat = asyncio.create_task(self._heartbeat_loop(client))
            ota_writer = asyncio.create_task(self._ota_loop(client))
            stop_wait = asyncio.create_task(self._stop_event.wait())
            disconnect_wait = asyncio.create_task(disconnected.wait())
            done, pending = await asyncio.wait(
                {writer, heartbeat, ota_writer, stop_wait, disconnect_wait},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            for task in pending:
                with suppress(asyncio.CancelledError):
                    await task
            for task in done:
                if task in {writer, heartbeat, ota_writer}:
                    task.result()
            self._connected_client = None
            self._status_callback("蓝牙已断开")

    async def _writer_loop(self, client: BleakClient) -> None:
        while True:
            snapshot = await self._outgoing.get()
            async with self._write_lock:
                await self._write_snapshot(client, snapshot)

    async def _write_snapshot(self, client: BleakClient, snapshot: DashboardSnapshot) -> None:
            self._sequence = (self._sequence + 1) & 0xFF
            await client.write_gatt_char(
                BLEProtocol.CONTROL_UUID,
                BLEProtocol.encode_led_count(self._sequence, len(snapshot.tasks) + 1),
                response=True,
            )
            self._sequence = (self._sequence + 1) & 0xFF
            await client.write_gatt_char(
                BLEProtocol.CONTROL_UUID,
                BLEProtocol.encode_sleep_timeout(self._sequence, snapshot.sleep_timeout_minutes),
                response=True,
            )
            for state, style in snapshot.state_styles.items():
                self._sequence = (self._sequence + 1) & 0xFF
                await client.write_gatt_char(
                    BLEProtocol.CONTROL_UUID,
                    BLEProtocol.encode_state_style(
                        self._sequence, int(state), style.color, style.effect,
                        style.period_ms, style.blink_duty_percent,
                    ),
                    response=True,
                )
            self._sequence = (self._sequence + 1) & 0xFF
            await client.write_gatt_char(
                BLEProtocol.CONTROL_UUID,
                BLEProtocol.encode_panel_header(self._sequence, snapshot),
                response=True,
            )
            for task_index, task in enumerate(snapshot.tasks):
                self._sequence = (self._sequence + 1) & 0xFF
                await client.write_gatt_char(
                    BLEProtocol.CONTROL_UUID,
                    BLEProtocol.encode_task_state(self._sequence, task_index, task),
                    response=True,
                )
            self._status_callback("六灯状态已发送")

    async def _ota_loop(self, client: BleakClient) -> None:
        while True:
            firmware = await self._ota_outgoing.get()
            async with self._write_lock:
                characteristic = client.services.get_characteristic(BLEProtocol.OTA_UUID)
                if characteristic is None:
                    raise RuntimeError("灯板固件不支持蓝牙 OTA")
                max_write = int(getattr(characteristic, "max_write_without_response_size", 20))
                chunk_size = max(16, min(240, max_write - 1))
                await client.write_gatt_char(
                    BLEProtocol.OTA_UUID, BLEProtocol.encode_ota_start(len(firmware)), response=True
                )
                sent = 0
                last_percent = -1
                try:
                    while sent < len(firmware):
                        chunk = firmware[sent:sent + chunk_size]
                        await client.write_gatt_char(
                            BLEProtocol.OTA_UUID, BLEProtocol.encode_ota_data(chunk), response=True
                        )
                        sent += len(chunk)
                        percent = sent * 100 // len(firmware)
                        if percent >= last_percent + 5:
                            last_percent = percent
                            self._status_callback(f"蓝牙升级写入 {percent}%")
                    await client.write_gatt_char(
                        BLEProtocol.OTA_UUID, BLEProtocol.encode_ota_finish(), response=True
                    )
                    self._status_callback("固件校验通过，灯板正在重启")
                except Exception:
                    with suppress(Exception):
                        await client.write_gatt_char(
                            BLEProtocol.OTA_UUID, BLEProtocol.encode_ota_abort(), response=True
                        )
                    raise

    async def _heartbeat_loop(self, client: BleakClient) -> None:
        while True:
            await asyncio.sleep(5)
            async with self._write_lock:
                self._sequence = (self._sequence + 1) & 0xFF
                await client.write_gatt_char(
                    BLEProtocol.CONTROL_UUID,
                    BLEProtocol.encode_heartbeat(self._sequence),
                    response=False,
                )
