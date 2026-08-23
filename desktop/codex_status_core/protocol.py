from __future__ import annotations

from .models import DashboardSnapshot


class BLEProtocol:
    """不依赖操作系统的 ESP32 蓝牙协议编码器。"""

    DEVICE_NAME_PREFIX = "Codex-Light-"

    @classmethod
    def device_id_from_name(cls, name: str | None) -> str | None:
        """从广播名中提取芯片唯一短 ID。"""
        if not name or not name.startswith(cls.DEVICE_NAME_PREFIX):
            return None
        device_id = name[len(cls.DEVICE_NAME_PREFIX):].upper()
        if len(device_id) != 6 or any(ch not in "0123456789ABCDEF" for ch in device_id):
            return None
        return device_id

    @classmethod
    def encode_identify(cls, sequence: int) -> bytes:
        """让当前连接的灯板播放 3 秒白色流水，用于现场认领。"""
        return bytes((0xC3, 1, 4, sequence & 0xFF))

    @classmethod
    def encode_state_style(
        cls, sequence: int, state: int, color: tuple[int, int, int], effect: int,
        period_ms: int = 1200, blink_duty_percent: int = 15,
    ) -> bytes:
        """覆盖状态的颜色、行为、动画周期，以及闪烁时的亮灯占空比。"""
        if not 1 <= state <= 5 or not 1 <= effect <= 3:
            raise ValueError("状态或灯效编号无效")
        if len(color) != 3 or any(channel < 0 or channel > 255 for channel in color):
            raise ValueError("RGB 颜色无效")
        if effect != 1 and not 200 <= period_ms <= 10000:
            raise ValueError("动画周期必须在 200~10000 毫秒之间")
        if effect == 2 and not 1 <= blink_duty_percent <= 100:
            raise ValueError("闪烁占空比必须在 1~100 之间")
        return bytes((0xC3, 1, 5, sequence & 0xFF, state, *color, effect,
                      period_ms & 0xFF, (period_ms >> 8) & 0xFF, blink_duty_percent))

    @classmethod
    def encode_panel_header(cls, sequence: int, snapshot: DashboardSnapshot) -> bytes:
        snapshot.validate()
        return bytes((0xC3, 1, 7, sequence & 0xFF, snapshot.remaining_percent,
                      snapshot.period_used_percent, snapshot.master_brightness_percent))

    @classmethod
    def encode_task_state(
        cls, sequence: int, task_index: int, task: object, *, include_timing_mode: bool = True
    ) -> bytes:
        if not 0 <= task_index <= 62:
            raise ValueError("任务索引必须在 0~62 之间")
        task.validate()
        packet = bytes((0xC3, 1, 6, sequence & 0xFF, task_index,
                        int(task.state), task.progress,
                        task.animation_period_ms & 0xFF,
                        (task.animation_period_ms >> 8) & 0xFF))
        return packet + bytes((0 if task.automatic_frequency else 1,)) if include_timing_mode else packet

    @classmethod
    def encode_led_count(cls, sequence: int, total_count: int) -> bytes:
        if not 2 <= total_count <= 64:
            raise ValueError("灯珠总数必须在 2~64 之间")
        return bytes((0xC3, 1, 8, sequence & 0xFF, total_count))

    @classmethod
    def encode_sleep_timeout(cls, sequence: int, minutes: int) -> bytes:
        """配置断连后进入深度睡眠的等待时间，并由固件保存到 Flash。"""
        if not 1 <= minutes <= 1440:
            raise ValueError("断连休眠时间必须在 1~1440 分钟之间")
        return bytes((0xC3, 1, 9, sequence & 0xFF, minutes & 0xFF, minutes >> 8))

    @classmethod
    def encode_channel_count(cls, sequence: int, channels: int) -> bytes:
        """配置 GPIO8 单通道或 GPIO8+GPIO10 双通道输出。"""
        if channels not in (1, 2):
            raise ValueError("灯带通道数必须是 1 或 2")
        return bytes((0xC3, 1, 10, sequence & 0xFF, channels))
    SERVICE_UUID = "0100c310-7625-819e-934c-32b8e4177d6a"
    CONTROL_UUID = "0200c310-7625-819e-934c-32b8e4177d6a"
    OTA_UUID = "0300c310-7625-819e-934c-32b8e4177d6a"
    INFO_UUID = "0400c310-7625-819e-934c-32b8e4177d6a"

    @staticmethod
    def encode_ota_start(size: int) -> bytes:
        if not 1 <= size <= 2 * 1024 * 1024:
            raise ValueError("OTA 固件大小无效")
        return bytes((1, size & 0xFF, (size >> 8) & 0xFF, (size >> 16) & 0xFF, size >> 24))

    @staticmethod
    def encode_ota_data(chunk: bytes) -> bytes:
        if not chunk or len(chunk) > 511:
            raise ValueError("OTA 数据块大小无效")
        return bytes((2,)) + chunk

    @staticmethod
    def encode_ota_finish() -> bytes:
        return bytes((3,))

    @staticmethod
    def encode_ota_abort() -> bytes:
        return bytes((4,))

    _MAGIC = 0xC3
    _VERSION = 0x01
    _HEARTBEAT = 0x01
    _SNAPSHOT = 0x02

    @classmethod
    def encode_heartbeat(cls, sequence: int) -> bytes:
        return bytes((cls._MAGIC, cls._VERSION, cls._HEARTBEAT, sequence & 0xFF))

    @classmethod
    def encode_snapshot(cls, sequence: int, snapshot: DashboardSnapshot) -> bytes:
        snapshot.validate()
        packet = bytearray(
            (
                cls._MAGIC,
                cls._VERSION,
                cls._SNAPSHOT,
                sequence & 0xFF,
                snapshot.remaining_percent,
                snapshot.period_used_percent,
            )
        )
        packet.extend(int(task.state) for task in snapshot.tasks)
        packet.extend(task.progress for task in snapshot.tasks)
        packet.append(snapshot.master_brightness_percent)
        if len(packet) != 17:
            raise AssertionError("完整快照必须固定为 17 字节")
        return bytes(packet)
