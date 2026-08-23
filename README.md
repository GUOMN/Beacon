# ESP32-C3 WS2812 蓝牙状态面板

基于 ESP-IDF 的 ESP32-C3 SuperMini 状态灯项目。默认使用 6 颗串行 WS2812：
第一颗显示系统用量，其余灯珠显示任务状态。灯珠总数可由桌面端设置为
2～64，新增灯珠自动作为任务灯使用。

## 主要功能

- 纯蓝牙低功耗通信，不依赖 Wi-Fi。
- 每块板使用芯片唯一 ID，便于多设备识别和绑定。
- 任务状态统一配置颜色与常亮、闪烁、呼吸行为。
- 桌面端可设置动画频率、闪烁占空比和整体亮度。
- 灯珠总数与状态主题保存到板载 Flash，掉电后自动恢复。
- Windows 11 桌面端已实现；跨平台核心可供 macOS 外壳复用。
- `diagnostic_idle` 提供关闭无线与灯带驱动的深度睡眠诊断固件。

## 接线

| ESP32-C3 SuperMini | WS2812 灯带 |
| --- | --- |
| GPIO 8（默认） | DIN |
| GND | GND |
| 外部稳定 5 V | 5 V |

灯带与开发板必须共地。建议在 DIN 串联 330～470 Ω 电阻，并在灯带电源入口
并联适当的大容量电容。多颗灯珠不要由开发板稳压器直接供电。

使用 CH340 烧录时，CH340 TXD 接板子 GPIO20，CH340 RXD 接 GPIO21，并共地。

## 构建与烧录

安装 ESP-IDF 5.5，进入已配置的 ESP-IDF 终端后运行：

```powershell
idf.py set-target esp32c3
idf.py build
idf.py -p COM5 flash monitor
```

将 `COM5` 替换为实际串口。通过没有自动复位线的 CH340 烧录时，需要手动让
开发板进入下载模式。

## 桌面端

Windows 客户端位于 `desktop`：

```powershell
cd desktop
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m windows_app.main
```

协议模型、蓝牙客户端和编码器位于 `desktop/codex_status_core`，界面位于
`desktop/windows_app`，协议单元测试位于 `desktop/tests`。
# 固件升级说明

- 第一次启用双分区 OTA，必须通过串口把 `ws2812_ota_initial_4mb.bin` 从 Flash 地址 `0x0` 写入。它包含引导程序、4 MB 分区表和应用程序。
- 后续在桌面端点击“蓝牙升级”，只选择 `ws2812_ota_update.bin`。不要把首次串口整包用于蓝牙升级。
- 升级期间保持灯板稳定供电和蓝牙连接；新固件校验成功后会自动切换并重启，启动失败时由回滚机制保留上一版。
- 自动休眠默认是断连 10 分钟。休眠后灯光与蓝牙均停止，短按 RESET 重新启动。
