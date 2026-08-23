# Beacon · 信标

[English](#english) · 中文

一个把电脑任务状态显示在 WS2812 灯带上的桌面状态面板。第一颗灯表示系统状态，后面的灯分别对应任务。

## 所需硬件

- ESP32-C3 SuperMini 开发板
- WS2812 / WS2812B 灯带，默认 6 颗，支持 2～64 颗
- 稳定的 5 V 灯带电源
- USB 数据线；首次烧录也可以使用 CH340 USB 转串口模块
- 推荐：330～470 Ω 数据线串联电阻、灯带电源入口滤波电容

## 物理连接

```text
                    ┌─────────────────────┐
电脑 USB ──────────▶│ ESP32-C3 SuperMini  │
                    │                     │
                    │ GPIO8  ─────────────┼────▶ 第一条 WS2812 DIN
                    │ GPIO10 ─────────────┼────▶ 第二条 WS2812 DIN（可选）
                    │ GND    ─────────────┼────▶ 灯带电源 GND
                    └─────────────────────┘

外部稳定 5 V ─────────────────────────────────▶ WS2812 5V
外部电源 GND ─────────────────────────────────▶ WS2812 GND / ESP32 GND
```

默认只启用 GPIO8。GPIO10 是可选的第二输出通道，需要在桌面端手动开启。两路通道显示相同的状态。

灯带和开发板必须共地。较长灯带不要直接由开发板稳压器供电。

使用 CH340 烧录时：CH340 TXD 接板子 GPIO20，CH340 RXD 接 GPIO21，并连接 GND。

## 灯位含义

- **第 1 颗灯（灯位 0）**：系统状态灯。颜色表示所选系统指标的可用程度，闪烁速度表示当前繁忙程度。
- **第 2 颗灯开始（灯位 1、2、3……）**：任务灯。颜色表示任务状态，常亮、闪烁或呼吸表示对应行为。

颜色、行为、频率、整体亮度和灯珠数量都可以在桌面端配置。

## 桌面端与灯板如何通信

```text
Codex / 其他任务工具
          │ 任务事件
          ▼
桌面客户端 ──▶ 本地数据库
     │
     │ Bluetooth Low Energy
     ▼
ESP32-C3 ──▶ 在本地运行灯光动画 ──▶ WS2812 灯带
```

1. 桌面端从本机任务工具获取状态，并保存在本地。
2. 每块灯板通过芯片唯一 ID 区分。首次使用时扫描、识别并绑定自己的灯板，避免办公室内同名设备串连。
3. 任务或配置变化时，桌面端通过 BLE 下发状态；灯光动画由 ESP32 本地持续运行，不需要电脑高频发送每一帧。
4. 灯数、状态灯效、系统灯效、输出通道和休眠时间会保存在灯板中；总亮度和当前任务状态属于运行数据，连接后由桌面端用最新值恢复。
5. 首次安装 OTA 固件后，后续固件可以直接通过桌面端蓝牙升级。

### BLE 控制协议概览

普通控制包写入控制特征（Control Characteristic），采用小端字节序。所有包都以相同的 4 字节包头开始：

| 字节 | 含义 | 说明 |
| --- | --- | --- |
| 0 | Magic | 固定为 `0xC3` |
| 1 | Version | 当前为 `0x01` |
| 2 | Type | 数据包类型 |
| 3 | Sequence | 0～255 循环递增，用于区分相邻命令 |

常用包类型：

| Type | 用途 | 包体（从字节 4 开始） | 是否写入灯板 Flash |
| --- | --- | --- | --- |
| `0x01` | 心跳 | 无 | 否，不触发灯效重算 |
| `0x05` | 状态灯效 | 状态、RGB、效果、周期、闪烁占空比 | 是 |
| `0x06` | 单个任务灯状态 | 灯位、任务状态、进度、周期、自动/手动模式 | 否 |
| `0x07` | 系统灯运行状态 | 指标可用度、繁忙度、总亮度 | 否 |
| `0x08` | 灯珠总数 | 总灯数 | 是 |
| `0x09` | 断连休眠 | 分钟数 | 是 |
| `0x0A` | 输出通道数 | `1` 或 `2` | 是 |
| `0x0B` | 系统灯效果 | 常亮、闪烁、呼吸或双闪 | 是 |

例如，下面的 12 字节数据将“进行中”状态设为蓝色呼吸，周期 1200 ms：

```text
C3 01 05 2A  01 5B 8F F9  03 B0 04 0F
│  │  │  │   │  └──RGB──┘  │  └─┬─┘ │
│  │  │  │   │              │  1200  占空比 15%
│  │  │  │   │              呼吸（3）
│  │  │  │   进行中（1）
│  │  │  Sequence = 42
│  │  Type = 状态灯效
│  Version = 1
Magic
```

任务灯运行包示例：

```text
C3 01 06 2B  00 01 64 52 03 00
               │  │  │  └─┬─┘ │
               │  │  │   850ms 自动频率（0）
               │  │  进度 100
               │  进行中（1）
               第 1 个任务灯（索引 0，对应物理灯位 1）
```

自动频率时，桌面端根据每个任务的工作量分别计算周期，所以同一状态的不同任务灯也可能具有不同速度；手动频率时，同一状态使用统一周期。ESP32 收到状态后自行生成每一帧动画，桌面端不传输动画帧。

### 什么时候下发数据

- 点击“保存并生效”时，桌面端重发一套完整配置和当前状态，避免此前某个包丢失后长期不一致。
- 任务、指标或预览数据发生变化时，桌面端下发最新运行状态；内容没有变化时不重复下发。
- 进入预览、修改预览或退出预览时，分别下发对应的预览状态或最新真实状态。ESP32 不区分预览包和正式包。
- 断线重连后，桌面端补发最新完整状态；空闲心跳只维持连接，不要求灯板重新计算灯效。
- 扫描、识别、手动连接/断开、配置保存和 OTA 属于前台操作。前台占用蓝牙连接时，后台任务刷新会退避；操作结束后只按最新状态继续同步。
- OTA 固件数据使用独立的 OTA 特征分片传输，不与上述普通控制包混用。

## 基本使用

1. 按上图接好灯带并给灯带稳定供电。
2. 安装首次固件并启动桌面客户端。
3. 打开“设备管理”，扫描后点击“识别”，确认对应灯板再绑定。
4. 在“配置”页设置灯数、亮度和灯效，点击“保存并生效”。
5. 在“任务面板”查看任务，也可以拖动排序或固定到指定灯位。

---

<a id="english"></a>

## English

Beacon displays computer task states on a WS2812 LED strip. The first LED represents system activity; every following LED represents a task.

### Hardware

- ESP32-C3 SuperMini
- WS2812 / WS2812B strip, 6 LEDs by default and configurable from 2 to 64
- Stable external 5 V supply for the strip
- USB data cable, or a CH340 USB-to-serial adapter for initial flashing
- Recommended: 330–470 Ω data resistor and a power-entry capacitor

### Wiring

| ESP32-C3 SuperMini | Connection |
| --- | --- |
| GPIO8 | Primary WS2812 DIN |
| GPIO10 | Optional secondary WS2812 DIN |
| GND | Common ground with the LED power supply |

Power the strip from a stable external 5 V supply and connect all grounds together. Do not power a long strip through the board regulator.

For CH340 flashing, connect CH340 TXD to GPIO20, CH340 RXD to GPIO21, and connect GND.

### Communication model

The desktop app collects local task events, stores them locally, and sends state changes to the bound ESP32-C3 over Bluetooth Low Energy. Each board exposes a unique chip-derived ID, so multiple boards with the same visible name can be identified and bound safely. The ESP32 renders animations locally; the computer does not stream every animation frame.

LED count, state effects, the system effect, output channels, and sleep timeout persist on the board. Master brightness and current task states are runtime values restored by the desktop app after connection. After the initial OTA-capable firmware is installed, later firmware updates can be sent over Bluetooth from the desktop app.

### BLE protocol summary

Normal control messages are written to the Control Characteristic. Each packet begins with `C3 01 TYPE SEQUENCE`: a fixed magic byte, protocol version, packet type, and an incrementing sequence byte. Configuration packets cover LED count, sleep timeout, output channels, system effect, and per-state RGB/effect/timing. Runtime packets carry system availability/activity/brightness and each task LED's state, progress, timing, and automatic/manual timing mode.

Choosing **Save and Apply** retransmits the complete configuration and current state. Runtime updates are sent only when their data changes, and the latest full state is restored after reconnection. Scanning, identifying, manual connection changes, configuration writes, and OTA have foreground priority; background task refresh waits while one of these operations owns the BLE connection. The ESP32 renders all animation frames locally, so the desktop app never streams individual frames. OTA uses its own characteristic and chunk format.

### Basic use

1. Wire and power the LED strip.
2. Install the initial firmware and open the desktop app.
3. Scan in **Device Manager**, identify the physical board, and bind it.
4. Configure the strip and choose **Save and Apply**.
5. Monitor, reorder, or pin tasks in the task panel.
