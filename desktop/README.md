# Codex Status Bridge

Codex 状态灯本地桥接软件，采用“跨平台后台核心 + 平台外壳”结构。

## 目录

- `codex_status_core/`：灯板模型、蓝牙协议、自动重连、SQLite 事件服务与官方 Hook 适配，Windows 和 macOS 共用。
- `windows_app/`：Windows 11 的轻量桌面界面。
- `tests/`：不依赖蓝牙硬件的协议测试。
- `CodexStatusBridge.spec`：生成独立 Windows 程序的打包配置。

## 当前功能

- 按唯一设备 ID 扫描、识别、绑定并自动重连灯板。
- 断线自动重连，每 5 秒发送心跳。
- 内置私有 SQLite 和本地事件入口，不依赖其他工具的数据库。
- 支持显式启用 Claude Code、Gemini CLI、Cursor、GitHub Copilot CLI 官方 Hook；原配置合并保留并自动备份。
- Codex 使用官方 `notify` 链式回调感知每轮结束，无需额外安装 Skill，并继续保留原有 Codex 通知程序。
- Hook 只上传任务标识、来源、状态和时间，不保存提示词正文，失败也不会阻断原任务。
- 进行中、等待、完成、警告和失败自动映射到任务灯，最近五小时事件映射为短时繁忙程度。
- 每块灯板独立保存色彩校准参数。
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

Mac 版本复用完整的 `codex_status_core`，仅增加 macOS 打包入口、窗口和系统权限提示；任务采集、状态映射、蓝牙与 ESP32 协议均不重写。

## 数据流与后续网络扩展

当前数据流为 `官方 Hook → 本地 HTTP 事件入口 → 应用 SQLite → 蓝牙灯板`。事件入口与灯板传输解耦，后续可以增加 MQTT 或网络灯板传输层，无需修改各工具的 Hook 事件格式。
