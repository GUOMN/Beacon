# Codex Status Bridge for macOS

原生 SwiftUI + CoreBluetooth 上位软件，用于把用量和五个 Codex 任务状态发送到 ESP32-C3 六灯面板。

## 在 Mac 上运行

1. 使用 Xcode 打开本目录的 `Package.swift`。
2. 选择 `CodexStatusBridge` scheme 和本机 Mac 目标。
3. 首次运行时允许应用使用蓝牙。
4. 给 ESP32-C3 上电，应用会自动搜索并连接 `Codex-Status-6`。

当前版本提供手动状态面板，用于先验证 Mac、蓝牙和 ESP32 的完整链路。下一阶段的 Codex 插件只需调用 `AppModel` 的状态入口，不需要修改蓝牙协议。

## 六灯定义

- 第一颗：颜色由绿色连续过渡到红色表示剩余额度；闪烁越快表示短周期用量越高。
- 第二至第六颗：五个任务槽位。
  - 熄灭：无任务
  - 蓝色呼吸：进行中
  - 黄色常亮：等待操作
  - 绿色呼吸：完成
  - 红色常亮：失败

## 蓝牙协议

- 设备名：`Codex-Status-6`
- 心跳每 5 秒发送一次。
- 完整快照固定 16 字节，能够在默认 BLE MTU 下单包完成。
