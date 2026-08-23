#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"
#include "led_status.h"

/* 六灯面板中每颗灯的固定职责，编号与灯带物理顺序一致。 */
typedef enum {
    PANEL_LED_USAGE = 0,  // Token 或套餐用量告警
    PANEL_LED_TASK_1 = 1, // 最近任务一
    PANEL_LED_TASK_2 = 2, // 最近任务二
    PANEL_LED_TASK_3 = 3, // 最近任务三
    PANEL_LED_TASK_4 = 4, // 最近任务四
    PANEL_LED_TASK_5 = 5, // 最近任务五
} panel_led_role_t;

/* 插件发送的是语义状态，由 ESP32 统一决定颜色和灯效。 */
typedef enum {
    PANEL_STATE_IDLE = 0,
    PANEL_STATE_RUNNING,
    PANEL_STATE_WAITING,
    PANEL_STATE_SUCCESS,
    PANEL_STATE_WARNING,
    PANEL_STATE_ERROR,
} panel_state_t;

// 从 Flash NVS 恢复上位机保存的状态主题；没有保存值时继续使用固件默认值。
esp_err_t dashboard_status_load_saved_styles(void);

// 设置一颗业务灯；进度范围为 0~100
esp_err_t dashboard_status_set(uint8_t led_index, panel_state_t state, uint8_t progress);
esp_err_t dashboard_status_set_with_period(uint8_t led_index, panel_state_t state,
                                           uint8_t progress, uint16_t period_ms);
// 设置任务灯动画计时方式。自动模式忽略 period_ms，由固件逐任务计算；
// 手动模式使用上位机传入的状态级 period_ms。
esp_err_t dashboard_status_set_with_timing(uint8_t led_index, panel_state_t state,
                                           uint8_t progress, uint16_t period_ms,
                                           led_animation_timing_t timing_mode);

// 第一颗灯：颜色表示剩余百分比，呼吸速度表示短周期已用百分比
esp_err_t dashboard_status_set_usage(uint8_t remaining_percent,
                                     uint8_t period_used_percent);

// 一次更新第一颗用量灯与后五颗任务灯
esp_err_t dashboard_status_set_snapshot(uint8_t remaining_percent,
                                        uint8_t period_used_percent,
                                        const uint8_t task_states[5],
                                        const uint8_t task_progress[5]);

// 覆盖某个业务状态的颜色和行为；未收到覆盖时使用固件默认值
esp_err_t dashboard_status_set_state_style(panel_state_t state,
                                           uint8_t red, uint8_t green, uint8_t blue,
                                           led_effect_t effect, uint16_t period_ms,
                                           uint8_t blink_duty_percent);

// 蓝牙未连接或数据超时时，六颗灯统一显示等待动画
esp_err_t dashboard_status_set_connection(bool connected, bool data_alive,
                                          bool abnormal_disconnect);
