#pragma once

#include <stdint.h>

#include "esp_err.h"
#include "led_strip.h"

// 单颗灯支持的显示效果
typedef enum {
    LED_EFFECT_OFF = 0,
    LED_EFFECT_SOLID,
    LED_EFFECT_BLINK,
    LED_EFFECT_BREATHE,
} led_effect_t;

// 单颗灯的完整状态；蓝牙和本地逻辑都使用这一个结构
typedef struct {
    uint8_t red;
    uint8_t green;
    uint8_t blue;
    uint8_t brightness;   // 单灯亮度，范围 0~255
    led_effect_t effect;
    uint16_t period_ms;   // 闪烁或呼吸周期；常亮和关闭时忽略
    uint8_t blink_duty_percent; // 闪烁亮灯占空比 1~100；0 表示使用固件短脉冲默认值
    uint16_t phase_offset_ms; // 动画相位偏移，用于形成沿灯带移动的呼吸流水
} led_status_t;

// 启动六灯渲染任务
esp_err_t led_status_start(led_strip_handle_t strip);

// 设置或读取指定灯珠的状态，灯珠编号为 0~5
esp_err_t led_status_set(uint8_t index, const led_status_t *status);
esp_err_t led_status_get(uint8_t index, led_status_t *status);
esp_err_t led_status_set_master_brightness(uint8_t percent);

// 修改或读取当前实际灯珠数，固件内部最多预留 64 颗
esp_err_t led_status_set_active_count(uint8_t count);
uint8_t led_status_get_active_count(void);

// 便捷接口：把一个进度值映射到六颗灯
esp_err_t led_status_set_progress(uint32_t completed,
                                  uint32_t total,
                                  const led_status_t *completed_status,
                                  const led_status_t *pending_status);
