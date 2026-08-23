# Tauri + React 桌面界面

Windows 与 macOS 共用这一套 React 页面和 Tauri 外壳。旧的 Tk 客户端仍保留，Python `codex_status_core` 仍是任务采集、SQLite、Hook 和蓝牙的唯一后台核心。

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

Tauri 通过 `desktop/tauri_bridge.py` 调用现有 Python 核心。任务面板每两秒读取应用私有 SQLite，Codex 会话采集器或事件 Hook 写入后会自动显示；设备扫描复用 `windows_app.ble_worker`，不在前端复制协议逻辑。

运行前安装依赖：

```powershell
python -m pip install -r ../requirements.txt
```

Python 或 BLE 依赖不可用时，界面会显示后台错误；任务读取与蓝牙扫描彼此隔离，缺少 `bleak` 不影响 SQLite 任务面板。
