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
4. 灯数、亮度、灯效、输出通道和休眠时间会保存在灯板中，断连或重启后仍可恢复。
5. 首次安装 OTA 固件后，后续固件可以直接通过桌面端蓝牙升级。

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

LED count, brightness, effects, output channels, and sleep timeout persist on the board. After the initial OTA-capable firmware is installed, later firmware updates can be sent over Bluetooth from the desktop app.

### Basic use

1. Wire and power the LED strip.
2. Install the initial firmware and open the desktop app.
3. Scan in **Device Manager**, identify the physical board, and bind it.
4. Configure the strip and choose **Save and Apply**.
5. Monitor, reorder, or pin tasks in the task panel.
