# Codex Status Bridge

Windows 11 首版上位软件，采用“跨平台核心 + 平台外壳”结构。

## 目录

- `codex_status_core/`：六灯模型、16 字节协议、蓝牙自动重连与心跳，Windows 和 macOS 共用。
- `windows_app/`：Windows 11 的轻量桌面界面。
- `tests/`：不依赖蓝牙硬件的协议测试。
- `CodexStatusBridge.spec`：生成独立 Windows 程序的打包配置。

## 当前功能

- 自动搜索并连接 `Codex-Status-6`。
- 断线自动重连，每 5 秒发送心跳。
- 手动调整第一颗灯的剩余额度与短周期用量。
- 手动设置五个任务的状态和进度并发送完整快照。
- 保留连接日志，方便调试 ESP32 和电脑蓝牙。

## 开发运行

在 `desktop` 目录安装依赖后执行：

```powershell
python -m windows_app.main
```

## 打包

```powershell
pyinstaller --clean --noconfirm CodexStatusBridge.spec
```

打包结果为 `dist/CodexStatusBridge.exe`，运行电脑不需要预装 Python。

## 后续 Mac 外壳

Mac 版本复用 `codex_status_core`，仅更换窗口、菜单栏和系统权限提示等界面层；ESP32 协议不变。
