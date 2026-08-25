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

## macOS 快捷操作小组件

macOS 14 及以上版本的应用包会自动包含 `Beacon 快捷操作` WidgetKit 扩展。小、中、大三种尺寸分别提供任务概览、固定操作，以及与托盘快捷操作窗口一致的上移、下移、固定、删除和清理已完成任务。

主应用与扩展通过 `group.com.codexstatus.bridge` App Group 共享任务快照和操作请求。正式签名或发布前，需要在 Apple Developer 证书配置中为以下两个 Bundle ID 启用同一个 App Group：

- `com.codexstatus.bridge`
- `com.codexstatus.bridge.quick-actions`

构建脚本会复用 Tauri 的 `APPLE_SIGNING_IDENTITY`，并可通过 `APPLE_DEVELOPMENT_TEAM` 指定开发团队。没有签名身份时仍可完成本地编译和包结构验证，但 macOS 不保证加载无有效 App Group 签名的小组件。

小组件操作由常驻托盘的 Beacon 主进程执行，因此退出 Beacon 后仍可查看最后一次快照，但交互会等到应用下次启动后处理。安装应用后，在桌面右键选择“编辑小组件”，搜索 `Beacon` 即可添加；同一个小组件也可放入右侧通知中心。

## Python 后台

Tauri 通过 `desktop/tauri_bridge.py` 调用 Python 业务核心。任务面板每两秒读取应用私有 SQLite，Codex 会话采集器或事件 Hook 写入后会自动显示。Python 只生成待发送的协议包，实际蓝牙传输始终由 Rust 主进程完成。

运行前安装依赖：

```powershell
python -m pip install -r ../requirements.txt
```

Python 后台不可用时，界面会显示任务数据错误；蓝牙由应用主进程独立管理，不依赖用户安装 Python 或 Bleak。
