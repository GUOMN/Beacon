"""macOS ARM64 打包入口。

界面暂时复用已经验证过的跨平台 Tk 外壳；业务核心、SQLite、Codex 数据源、
蓝牙协议和托盘逻辑均来自共享模块，避免维护两套实现。
"""

from windows_app.main import main


if __name__ == "__main__":
    main()
