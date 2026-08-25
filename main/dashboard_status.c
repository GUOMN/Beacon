#include "dashboard_status.h"

#include "app_config.h"
#include "esp_check.h"
#include "led_status.h"
#include "nvs.h"

static bool s_state_style_valid[PANEL_STATE_ERROR + 1U];
static led_status_t s_state_styles[PANEL_STATE_ERROR + 1U];
static led_effect_t s_system_effect = LED_EFFECT_DOUBLE_BLINK;
static uint8_t s_system_brightness_percent = 100U;

// 上位机不覆盖动画参数时采用的固件默认值。
#define DEFAULT_TASK_ANIMATION_PERIOD_MS 1200U
#define DEFAULT_TASK_BLINK_DUTY_PERCENT  15U
#define SAVED_STYLE_VERSION               1U

typedef struct __attribute__((packed)) {
    uint8_t version;
    uint8_t red;
    uint8_t green;
    uint8_t blue;
    uint8_t effect;
    uint16_t period_ms;
    uint8_t blink_duty_percent;
} saved_style_t;

static const char *const s_style_keys[PANEL_STATE_ERROR + 1U] = {
    NULL, "style_1", "style_2", "style_3", "style_4", "style_5",
};

esp_err_t dashboard_status_load_saved_styles(void)
{
    nvs_handle_t handle;
    esp_err_t result = nvs_open("status_panel", NVS_READONLY, &handle);
    if (result == ESP_ERR_NVS_NOT_FOUND) {
        return ESP_OK;
    }
    ESP_RETURN_ON_ERROR(result, "dashboard", "打开状态主题存储失败");

    uint8_t saved_system_effect = 0U;
    if (nvs_get_u8(handle, "system_fx", &saved_system_effect) == ESP_OK &&
        saved_system_effect >= LED_EFFECT_SOLID &&
        saved_system_effect <= LED_EFFECT_DOUBLE_BLINK) {
        s_system_effect = (led_effect_t)saved_system_effect;
    }
    uint8_t saved_system_brightness = 0U;
    if (nvs_get_u8(handle, "system_br", &saved_system_brightness) == ESP_OK &&
        saved_system_brightness <= 100U) {
        s_system_brightness_percent = saved_system_brightness;
    }

    for (panel_state_t state = PANEL_STATE_RUNNING; state <= PANEL_STATE_ERROR; ++state) {
        saved_style_t saved;
        size_t size = sizeof(saved);
        if (nvs_get_blob(handle, s_style_keys[state], &saved, &size) == ESP_OK &&
            size == sizeof(saved) && saved.version == SAVED_STYLE_VERSION &&
            saved.effect >= LED_EFFECT_SOLID && saved.effect <= LED_EFFECT_DOUBLE_BLINK &&
            (saved.effect == LED_EFFECT_SOLID ||
             (saved.period_ms >= 200U && saved.period_ms <= 10000U)) &&
            (saved.effect != LED_EFFECT_BLINK ||
             (saved.blink_duty_percent >= 1U && saved.blink_duty_percent <= 100U))) {
            s_state_styles[state] = (led_status_t) {
                .red = saved.red, .green = saved.green, .blue = saved.blue,
                .brightness = 255,
                .effect = (led_effect_t)saved.effect,
                .period_ms = saved.effect == LED_EFFECT_SOLID ? 0U : saved.period_ms,
                .blink_duty_percent = saved.effect == LED_EFFECT_BLINK
                                              ? saved.blink_duty_percent : 0U,
            };
            s_state_style_valid[state] = true;
        }
    }
    nvs_close(handle);
    return ESP_OK;
}

static led_status_t semantic_to_led(panel_state_t state, uint8_t progress)
{
    if (progress > 100U) {
        progress = 100U;
    }

    // 运行时用亮度粗略表达进度，保证低进度时仍然清晰可见。
    const uint8_t progress_brightness = (uint8_t)(80U + (progress * 175U) / 100U);

    led_status_t result;
    switch (state) {
    case PANEL_STATE_RUNNING:
        result = (led_status_t) {
            .red = 0, .green = 90, .blue = 255,
            .brightness = progress_brightness,
            .effect = LED_EFFECT_BREATHE, .period_ms = 1200,
        };
        break;
    case PANEL_STATE_WAITING:
        result = (led_status_t) {
            .red = 255, .green = 150, .blue = 0,
            .brightness = 220,
            .effect = LED_EFFECT_SOLID, .period_ms = 0,
        };
        break;
    case PANEL_STATE_SUCCESS:
        result = (led_status_t) {
            .red = 0, .green = 255, .blue = 60,
            .brightness = 200,
            .effect = LED_EFFECT_BREATHE, .period_ms = 1600,
        };
        break;
    case PANEL_STATE_WARNING:
        result = (led_status_t) {
            .red = 255, .green = 80, .blue = 0,
            .brightness = 210,
            .effect = LED_EFFECT_BREATHE, .period_ms = 900,
        };
        break;
    case PANEL_STATE_ERROR:
        result = (led_status_t) {
            .red = 255, .green = 0, .blue = 0,
            .brightness = 240,
            .effect = LED_EFFECT_SOLID, .period_ms = 0,
        };
        break;
    case PANEL_STATE_IDLE:
    default:
        result = (led_status_t) {
            .red = 0, .green = 0, .blue = 0,
            .brightness = 0,
            .effect = LED_EFFECT_OFF, .period_ms = 0,
        };
        break;
    }

    if (state != PANEL_STATE_IDLE && s_state_style_valid[state]) {
        result.red = s_state_styles[state].red;
        result.green = s_state_styles[state].green;
        result.blue = s_state_styles[state].blue;
        result.effect = s_state_styles[state].effect;
        result.period_ms = s_state_styles[state].period_ms;
        result.blink_duty_percent = s_state_styles[state].blink_duty_percent;
    }
    return result;
}

esp_err_t dashboard_status_set(uint8_t led_index, panel_state_t state, uint8_t progress)
{
    return dashboard_status_set_with_period(led_index, state, progress, 0U);
}

esp_err_t dashboard_status_set_with_period(uint8_t led_index, panel_state_t state,
                                           uint8_t progress, uint16_t period_ms)
{
    // 兼容旧版扩展包：显式给周期视为手动，0 则沿用自动策略。
    return dashboard_status_set_with_timing(
        led_index, state, progress, period_ms,
        period_ms == 0U ? LED_ANIMATION_TIMING_AUTO
                        : LED_ANIMATION_TIMING_MANUAL);
}

esp_err_t dashboard_status_set_with_timing(uint8_t led_index, panel_state_t state,
                                           uint8_t progress, uint16_t period_ms,
                                           led_animation_timing_t timing_mode)
{
    ESP_RETURN_ON_FALSE(led_index < led_status_get_active_count(),
                        ESP_ERR_INVALID_ARG, "dashboard", "灯珠编号无效");
    ESP_RETURN_ON_FALSE(state <= PANEL_STATE_ERROR && progress <= 100U,
                        ESP_ERR_INVALID_ARG, "dashboard", "状态或进度无效");
    ESP_RETURN_ON_FALSE(timing_mode <= LED_ANIMATION_TIMING_MANUAL,
                        ESP_ERR_INVALID_ARG, "dashboard", "动画计时模式无效");
    led_status_t led = semantic_to_led(state, progress);
    led.timing_mode = timing_mode;
    if (timing_mode == LED_ANIMATION_TIMING_MANUAL &&
        led.effect != LED_EFFECT_SOLID && led.effect != LED_EFFECT_OFF) {
        ESP_RETURN_ON_FALSE(period_ms >= 200U && period_ms <= 10000U,
                            ESP_ERR_INVALID_ARG, "dashboard", "任务动画周期超出范围");
        led.period_ms = period_ms;
    } else if (timing_mode == LED_ANIMATION_TIMING_AUTO &&
               led.effect != LED_EFFECT_SOLID && led.effect != LED_EFFECT_OFF) {
        // 自动模式按任务进度与灯位生成稳定但彼此独立的频率。
        // 进度越高动画越快；灯位扰动避免多个进行中任务机械同频。
        const uint16_t progress_period_ms = (uint16_t)(2200U - progress * 12U);
        led.period_ms = (uint16_t)(progress_period_ms + (led_index * 173U) % 431U);
        led.phase_offset_ms = 0U;
    }
    return led_status_set(led_index, &led);
}

esp_err_t dashboard_status_set_state_style(panel_state_t state,
                                           uint8_t red, uint8_t green, uint8_t blue,
                                           led_effect_t effect, uint16_t period_ms,
                                           uint8_t blink_duty_percent)
{
    ESP_RETURN_ON_FALSE(state > PANEL_STATE_IDLE && state <= PANEL_STATE_ERROR &&
                            effect >= LED_EFFECT_SOLID && effect <= LED_EFFECT_DOUBLE_BLINK,
                        ESP_ERR_INVALID_ARG, "dashboard", "状态主题数据无效");
    if (effect != LED_EFFECT_SOLID) {
        ESP_RETURN_ON_FALSE(period_ms >= 200U && period_ms <= 10000U,
                            ESP_ERR_INVALID_ARG, "dashboard", "动画周期超出范围");
    }
    if (effect == LED_EFFECT_BLINK) {
        ESP_RETURN_ON_FALSE(blink_duty_percent >= 1U && blink_duty_percent <= 100U,
                            ESP_ERR_INVALID_ARG, "dashboard", "闪烁占空比超出范围");
    }
    const led_status_t new_style = (led_status_t) {
        .red = red, .green = green, .blue = blue,
        .brightness = 255,
        .effect = effect,
        .period_ms = effect == LED_EFFECT_SOLID ? 0U : period_ms,
        .blink_duty_percent = effect == LED_EFFECT_BLINK ? blink_duty_percent : 0U,
    };
    const bool changed = !s_state_style_valid[state] ||
                         s_state_styles[state].red != new_style.red ||
                         s_state_styles[state].green != new_style.green ||
                         s_state_styles[state].blue != new_style.blue ||
                         s_state_styles[state].effect != new_style.effect ||
                         s_state_styles[state].period_ms != new_style.period_ms ||
                         s_state_styles[state].blink_duty_percent !=
                             new_style.blink_duty_percent;
    s_state_styles[state] = new_style;
    s_state_style_valid[state] = true;

    if (changed) {
        const saved_style_t saved = {
            .version = SAVED_STYLE_VERSION,
            .red = red, .green = green, .blue = blue,
            .effect = (uint8_t)effect,
            .period_ms = new_style.period_ms,
            .blink_duty_percent = new_style.blink_duty_percent,
        };
        nvs_handle_t handle;
        ESP_RETURN_ON_ERROR(nvs_open("status_panel", NVS_READWRITE, &handle),
                            "dashboard", "打开状态主题存储失败");
        esp_err_t result = nvs_set_blob(handle, s_style_keys[state], &saved, sizeof(saved));
        if (result == ESP_OK) {
            result = nvs_commit(handle);
        }
        nvs_close(handle);
        ESP_RETURN_ON_ERROR(result, "dashboard", "保存状态主题失败");
    }
    return ESP_OK;
}

esp_err_t dashboard_status_set_usage(uint8_t remaining_percent,
                                     uint8_t period_used_percent)
{
    ESP_RETURN_ON_FALSE(remaining_percent <= 100U && period_used_percent <= 100U,
                        ESP_ERR_INVALID_ARG, "dashboard", "用量百分比无效");

    uint8_t red;
    uint8_t green;
    if (remaining_percent >= 50U) {
        // 余量 100% 到 50%：绿色连续过渡到黄色。
        red = (uint8_t)((100U - remaining_percent) * 255U / 50U);
        green = 255U;
    } else {
        // 余量 50% 到 0%：黄色连续过渡到红色。
        red = 255U;
        green = (uint8_t)(remaining_percent * 255U / 50U);
    }

    // 周期用量 0% 时约 3.2 秒一次呼吸，100% 时约 0.4 秒一次。
    const uint16_t period_ms = (uint16_t)(3200U - period_used_percent * 28U);
    const led_status_t status = {
        .red = red,
        .green = green,
        .blue = 0,
        .brightness = (uint8_t)(210U * s_system_brightness_percent / 100U),
        // 系统灯默认短亮两次再停顿，与任务灯的单闪和呼吸明确区分。
        .effect = s_system_effect,
        .period_ms = period_ms,
    };
    return led_status_set(PANEL_LED_USAGE, &status);
}

esp_err_t dashboard_status_set_system_effect(led_effect_t effect)
{
    ESP_RETURN_ON_FALSE(effect >= LED_EFFECT_SOLID && effect <= LED_EFFECT_DOUBLE_BLINK,
                        ESP_ERR_INVALID_ARG, "dashboard", "系统灯效无效");
    nvs_handle_t handle;
    ESP_RETURN_ON_ERROR(nvs_open("status_panel", NVS_READWRITE, &handle),
                        "dashboard", "打开系统灯效存储失败");
    esp_err_t result = nvs_set_u8(handle, "system_fx", (uint8_t)effect);
    if (result == ESP_OK) {
        result = nvs_commit(handle);
    }
    nvs_close(handle);
    if (result == ESP_OK) {
        s_system_effect = effect;
    }
    return result;
}

esp_err_t dashboard_status_set_system_brightness(uint8_t brightness_percent)
{
    ESP_RETURN_ON_FALSE(brightness_percent <= 100U, ESP_ERR_INVALID_ARG, "dashboard",
                        "系统灯亮度无效");
    nvs_handle_t handle;
    ESP_RETURN_ON_ERROR(nvs_open("status_panel", NVS_READWRITE, &handle), "dashboard",
                        "打开系统灯亮度存储失败");
    esp_err_t result = nvs_set_u8(handle, "system_br", brightness_percent);
    if (result == ESP_OK) {
        result = nvs_commit(handle);
    }
    nvs_close(handle);
    if (result == ESP_OK) {
        s_system_brightness_percent = brightness_percent;
    }
    return result;
}

esp_err_t dashboard_status_set_snapshot(uint8_t remaining_percent,
                                        uint8_t period_used_percent,
                                        const uint8_t task_states[5],
                                        const uint8_t task_progress[5])
{
    ESP_RETURN_ON_FALSE(task_states != NULL && task_progress != NULL,
                        ESP_ERR_INVALID_ARG, "dashboard", "快照数据为空");
    ESP_RETURN_ON_ERROR(dashboard_status_set_usage(remaining_percent, period_used_percent),
                        "dashboard", "应用用量状态失败");
    for (uint8_t i = 0; i < 5U; ++i) {
        ESP_RETURN_ON_ERROR(
            dashboard_status_set((uint8_t)(PANEL_LED_TASK_1 + i),
                                 (panel_state_t)task_states[i], task_progress[i]),
            "dashboard", "应用状态快照失败");
    }
    return ESP_OK;
}

esp_err_t dashboard_status_set_connection(bool connected, bool data_alive,
                                          bool abnormal_disconnect)
{
    // 连接并收到有效数据后不占用任何灯，六颗灯全部显示业务状态。
    if (connected && data_alive) {
        return ESP_OK;
    }

    const uint16_t flow_period_ms = 1800U;
    const uint8_t active_count = led_status_get_active_count();
    for (uint8_t i = 0; i < active_count; ++i) {
        const led_status_t status = {
            // 首次等待为蓝色；运行中断连或数据超时改为红色异常流水。
            .red = abnormal_disconnect ? 255 : 0,
            .green = abnormal_disconnect ? 0 : 60,
            .blue = abnormal_disconnect ? 0 : 255,
            .brightness = 170,
            .effect = LED_EFFECT_BREATHE,
            .period_ms = flow_period_ms,
            .phase_offset_ms = (uint16_t)((uint32_t)i * flow_period_ms / active_count),
        };
        ESP_RETURN_ON_ERROR(led_status_set(i, &status),
                            "dashboard", "设置连接状态流水失败");
    }
    return ESP_OK;
}
