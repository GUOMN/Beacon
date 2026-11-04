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

## 屏幕标记

Windows 和 macOS 上，按住 Alt（macOS 上是 Option），按住鼠标左键拖向轮盘的一格后松开，即可在起点显示标记。默认用 Ctrl + Alt + P 暂停或恢复。在「Ping」页可以录入自定义触发键和暂停快捷键，保存后立即生效。macOS 首次使用时，在 Beacon 左栏打开「Ping」，点击「打开辅助功能设置」。在列表中启用 Beacon，或点击「＋」从「应用程序」中选择 `Beacon.app`，再点「重试启动」。页面显示「全局输入已就绪」后即可使用。

要跨客户端共享标记，在 MQTT 设置中启用连接并填写同一个服务器地址和同一个 Ping Topic，例如 `beacon/ping/team-a`。状态 Topic 用于任务状态，需和 Ping Topic 分开。两者共用一个 MQTT 会话。接收端把远端标记投到主屏幕的相对位置。断线时，本地标记仍会显示。

输入辅助进程来自 lolPing v0.6.0，源码和 MIT 许可位于 `src-tauri/native/ping-hook`。构建 Windows 版需要 Visual Studio C++ 工具链，构建 macOS 版需要 Xcode 的 C++ 工具链。

选择鼠标侧键作为触发键时，按下侧键立即打开轮盘。移动鼠标选择，松开侧键或点击左键确认。此次侧键手势会拦截浏览器的前进或后退事件。

「Ping」页提供一个八向轮盘。内置「请看这里」「有个问题」「需要协助」「已完成」四个标记和短提示音，可把自定义图片或音效拖进槽位。自定义音效只在本机播放。同一个 Ping Topic 上的客户端会收到标记或自定义图片的静态图与效果。
