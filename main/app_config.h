#pragma once

#include "sdkconfig.h"

/* ==================== 用户常用配置 ==================== */

// 串行灯带上的总灯珠数：默认 6 颗，第 1 颗固定为系统灯，其余全是任务灯
// 以后扩展灯带时只需修改这一处并重新编译固件
#define STATUS_LED_DEFAULT_COUNT         6U

// 固件一次性预留的最大灯珠数；上位机可在 2~64 之间修改实际数量
#define STATUS_LED_MAX_COUNT             64U

// 全局最大亮度：范围 1~255，用于限制整条灯带的最高亮度
#define STATUS_LED_BRIGHTNESS            96U

// 第一颗系统灯的单次点亮时间：做成类似路由器指示灯的短脉冲
// 用量仍然只改变脉冲出现的周期，每次亮灯时间固定为 80 毫秒
#define STATUS_BLINK_PULSE_MS            80U

// 状态刷新周期：沿用工程 menuconfig 中的刷新间隔
#define STATUS_FRAME_MS                  CONFIG_WS2812_FRAME_INTERVAL_MS

// WS2812 数据引脚：沿用工程 menuconfig 中的 GPIO 配置
#define STATUS_DATA_GPIO                 CONFIG_WS2812_GPIO

// 蓝牙名称前缀；程序会在后面自动加上芯片唯一短 ID
#define STATUS_BLE_DEVICE_NAME_PREFIX    "Codex-Light-"

// 完整设备名最大长度：名称前缀 + 6 位十六进制唯一 ID + 结尾符
#define STATUS_BLE_DEVICE_NAME_MAX       24U

// 已连接但超过此时间没有收到插件数据时，连接灯显示紫色告警
#define STATUS_DATA_TIMEOUT_MS           15000U

// 超时检测任务的检查周期
#define STATUS_HEALTH_CHECK_MS           1000U

// 蓝牙断连后进入深度睡眠的默认等待时间；上位机可在 1~1440 分钟内覆盖并保存
#define STATUS_SLEEP_TIMEOUT_DEFAULT_MIN 10U
#define STATUS_SLEEP_TIMEOUT_MAX_MIN     1440U

// 上电硬件自检时间：六颗灯先固定点亮，随后进入蓝牙等待动画
#define STATUS_POWER_ON_TEST_MS          1500U

/* ==================== 驱动固定参数 ==================== */

#define STATUS_RMT_RESOLUTION_HZ         (10U * 1000U * 1000U)
#define STATUS_RMT_MEMORY_SYMBOLS        128U

#if STATUS_LED_DEFAULT_COUNT < 2U || STATUS_LED_DEFAULT_COUNT > STATUS_LED_MAX_COUNT
#error "STATUS_LED_DEFAULT_COUNT 必须在 2~STATUS_LED_MAX_COUNT 之间"
#endif

#if STATUS_LED_BRIGHTNESS == 0U || STATUS_LED_BRIGHTNESS > 255U
#error "STATUS_LED_BRIGHTNESS 必须在 1~255 之间"
#endif

#if STATUS_BLINK_PULSE_MS == 0U
#error "STATUS_BLINK_PULSE_MS 必须大于 0"
#endif
