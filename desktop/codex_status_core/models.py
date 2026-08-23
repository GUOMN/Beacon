from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class TaskState(IntEnum):
    """编号与 ESP32 固件中的 panel_state_t 完全一致。"""

    IDLE = 0
    RUNNING = 1
    WAITING = 2
    SUCCESS = 3
    WARNING = 4
    FAILURE = 5

    @property
    def chinese_name(self) -> str:
        return {
            TaskState.IDLE: "无任务",
            TaskState.RUNNING: "进行中",
            TaskState.WAITING: "等待操作",
            TaskState.SUCCESS: "已完成",
            TaskState.WARNING: "警告",
            TaskState.FAILURE: "失败",
        }[self]


@dataclass(slots=True)
class StateStyle:
    color: tuple[int, int, int]
    effect: int
    period_ms: int = 1200
    blink_duty_percent: int = 15

    def validate(self) -> None:
        if len(self.color) != 3 or any(channel < 0 or channel > 255 for channel in self.color):
            raise ValueError("RGB 颜色无效")
        if not 1 <= self.effect <= 3:
            raise ValueError("灯效必须是常亮、闪烁或呼吸")
        if self.effect != 1 and not 200 <= self.period_ms <= 10000:
            raise ValueError("动画周期必须在 200~10000 毫秒之间")
        if self.effect == 2 and not 1 <= self.blink_duty_percent <= 100:
            raise ValueError("闪烁占空比必须在 1~100 之间")


@dataclass(slots=True)
class TaskSlot:
    title: str
    state: TaskState = TaskState.IDLE
    progress: int = 0
    animation_period_ms: int = 0

    def validate(self) -> None:
        if not 0 <= self.progress <= 100:
            raise ValueError("任务进度必须在 0~100 之间")
        if self.animation_period_ms != 0 and not 200 <= self.animation_period_ms <= 10000:
            raise ValueError("任务独立动画周期必须为 0 或 200~10000 毫秒")


@dataclass(slots=True)
class DashboardSnapshot:
    """第一颗灯显示用量，后五颗灯显示任务。"""

    remaining_percent: int = 100
    period_used_percent: int = 0
    master_brightness_percent: int = 60
    sleep_timeout_minutes: int = 10
    state_styles: dict[TaskState, StateStyle] = field(default_factory=dict)
    tasks: list[TaskSlot] = field(
        default_factory=lambda: [TaskSlot(f"任务 {index + 1}") for index in range(5)]
    )

    def validate(self) -> None:
        if not 0 <= self.remaining_percent <= 100:
            raise ValueError("剩余额度必须在 0~100 之间")
        if not 0 <= self.period_used_percent <= 100:
            raise ValueError("周期用量必须在 0~100 之间")
        if not 0 <= self.master_brightness_percent <= 100:
            raise ValueError("整体亮度必须在 0~100 之间")
        if not 1 <= self.sleep_timeout_minutes <= 1440:
            raise ValueError("断连休眠时间必须在 1~1440 分钟之间")
        if not 1 <= len(self.tasks) <= 63:
            raise ValueError("任务灯数量必须在 1~63 之间")
        for task in self.tasks:
            task.validate()
        for state, style in self.state_styles.items():
            if state == TaskState.IDLE:
                raise ValueError("无任务状态固定熄灭，不允许覆盖")
            style.validate()
