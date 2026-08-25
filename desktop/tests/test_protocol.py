import unittest

from codex_status_core.models import DashboardSnapshot, TaskSlot, TaskState
from codex_status_core.protocol import BLEProtocol


class BLEProtocolTests(unittest.TestCase):
    def test_sleep_timeout_packet(self) -> None:
        self.assertEqual(BLEProtocol.encode_sleep_timeout(7, 10), bytes((0xC3, 1, 9, 7, 10, 0)))
        with self.assertRaises(ValueError):
            BLEProtocol.encode_sleep_timeout(0, 0)

    def test_system_effect_packet(self) -> None:
        self.assertEqual(BLEProtocol.encode_system_effect(8, 4), bytes((0xC3, 1, 11, 8, 4)))

    def test_system_brightness_packet(self) -> None:
        self.assertEqual(BLEProtocol.encode_system_brightness(9, 65), bytes((0xC3, 1, 12, 9, 65)))
        with self.assertRaises(ValueError):
            BLEProtocol.encode_system_brightness(9, 101)

    def test_ota_packets(self) -> None:
        self.assertEqual(BLEProtocol.encode_ota_start(0x123456), bytes((1, 0x56, 0x34, 0x12, 0)))
        self.assertEqual(BLEProtocol.encode_ota_data(b"abc"), b"\x02abc")
        self.assertEqual(BLEProtocol.encode_ota_finish(), b"\x03")
        self.assertEqual(BLEProtocol.encode_ota_abort(), b"\x04")

    def test_snapshot_is_exactly_sixteen_bytes(self) -> None:
        snapshot = DashboardSnapshot(
            master_brightness_percent=60,
            remaining_percent=72,
            period_used_percent=35,
            tasks=[
                TaskSlot("一", TaskState.RUNNING, 20),
                TaskSlot("二", TaskState.WAITING, 50),
                TaskSlot("三", TaskState.SUCCESS, 100),
                TaskSlot("四", TaskState.FAILURE, 80),
                TaskSlot("五", TaskState.IDLE, 0),
            ],
        )
        packet = BLEProtocol.encode_snapshot(9, snapshot)
        self.assertEqual(len(packet), 17)
        self.assertEqual(packet[:6], bytes((0xC3, 1, 2, 9, 72, 35)))
        self.assertEqual(packet[6:11], bytes((1, 2, 3, 5, 0)))
        self.assertEqual(packet[11:16], bytes((20, 50, 100, 80, 0)))
        self.assertEqual(packet[16], 60)

    def test_heartbeat_layout(self) -> None:
        self.assertEqual(BLEProtocol.encode_heartbeat(257), bytes((0xC3, 1, 1, 1)))

    def test_device_name_contains_stable_short_id(self) -> None:
        self.assertEqual(BLEProtocol.device_id_from_name("Codex-Light-A1B2C3"), "A1B2C3")
        self.assertIsNone(BLEProtocol.device_id_from_name("Codex-Status-6"))

    def test_identify_layout(self) -> None:
        self.assertEqual(BLEProtocol.encode_identify(258), bytes((0xC3, 1, 4, 2)))

    def test_scalable_task_packets(self) -> None:
        snapshot = DashboardSnapshot(tasks=[TaskSlot("任务")])
        self.assertEqual(len(BLEProtocol.encode_panel_header(1, snapshot)), 7)
        self.assertEqual(
            BLEProtocol.encode_task_state(2, 0, snapshot.tasks[0]),
            bytes((0xC3, 1, 6, 2, 0, 0, 0, 0, 0, 1)),
        )
        active = TaskSlot("大任务", TaskState.RUNNING, 0, animation_period_ms=850, automatic_frequency=True)
        self.assertEqual(
            BLEProtocol.encode_task_state(3, 1, active)[-3:],
            bytes((850 & 0xFF, 850 >> 8, 0)),
        )
        self.assertEqual(len(BLEProtocol.encode_task_state(3, 1, active, include_timing_mode=False)), 9)

    def test_state_style_packet(self) -> None:
        self.assertEqual(
            BLEProtocol.encode_state_style(3, 1, (10, 20, 30), 3, 1500, 20),
            bytes((0xC3, 1, 5, 3, 1, 10, 20, 30, 3, 0xDC, 0x05, 20)),
        )
        self.assertEqual(
            BLEProtocol.encode_state_style(4, 5, (255, 0, 0), 4, 1800, 15)[8],
            4,
        )


if __name__ == "__main__":
    unittest.main()
