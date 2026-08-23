# Tauri + React 桌面界面

Windows 与 macOS 共用这一套 React 页面和 Tauri 外壳。Tauri 的 Rust 主进程统一负责蓝牙权限、扫描、常驻连接、重连、数据写入、识别和 OTA；Python `codex_status_core` 只负责任务采集、SQLite、Hook 和业务状态计算。

## 开发运行

先安装 Node.js 20+、Rust stable，以及对应平台的 Tauri 2 系统依赖，然后：

```powershell
cd desktop/tauri_app
npm install
npm run tauri dev
```

构建安装包：

```powershell
npm run tauri build
```

## Python 后台

Tauri 通过 `desktop/tauri_bridge.py` 调用 Python 业务核心。任务面板每两秒读取应用私有 SQLite，Codex 会话采集器或事件 Hook 写入后会自动显示。Python 只生成待发送的协议包，实际蓝牙传输始终由 Rust 主进程完成。

运行前安装依赖：

```powershell
python -m pip install -r ../requirements.txt
```

Python 后台不可用时，界面会显示任务数据错误；蓝牙由应用主进程独立管理，不依赖用户安装 Python 或 Bleak。
