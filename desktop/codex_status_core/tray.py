from __future__ import annotations

from collections.abc import Callable

import pystray
from PIL import Image, ImageDraw


class TrayController:
    """Windows 托盘与 macOS 菜单栏共用的后台生命周期入口。"""

    def __init__(self, on_show: Callable[[], None], on_exit: Callable[[], None]) -> None:
        self._on_show = on_show
        self._on_exit = on_exit
        self._icon = pystray.Icon(
            "CodexStatusBridge",
            self._make_image(),
            "Codex 状态灯",
            menu=pystray.Menu(
                pystray.MenuItem("打开窗口", lambda _icon, _item: self._on_show(), default=True),
                pystray.MenuItem("退出", lambda _icon, _item: self._on_exit()),
            ),
        )

    @staticmethod
    def _make_image() -> Image.Image:
        image = Image.new("RGBA", (64, 64), (248, 248, 248, 255))
        draw = ImageDraw.Draw(image)
        colors = ((35, 99, 235), (34, 197, 94), (250, 204, 21), (239, 68, 68), (168, 85, 247), (6, 182, 212))
        positions = ((17, 19), (32, 15), (46, 23), (45, 40), (29, 48), (15, 38))
        for color, (x, y) in zip(colors, positions):
            draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill=(*color, 255))
        return image

    def start(self) -> None:
        self._icon.run_detached()

    def stop(self) -> None:
        self._icon.stop()

