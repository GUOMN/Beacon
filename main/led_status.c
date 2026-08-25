#include "led_status.h"

#include <math.h>
#include <stdbool.h>

#include "app_config.h"
#include "esp_check.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

static const char *TAG = "led_status";
static led_strip_handle_t s_strips[2];
static uint8_t s_channel_count = 1U;
static bool s_force_refresh = true;
static SemaphoreHandle_t s_status_mutex;
static led_status_t s_status[STATUS_LED_MAX_COUNT];
static uint8_t s_master_brightness_percent = STATUS_LED_DEFAULT_BRIGHTNESS_PERCENT;
static uint8_t s_active_count = STATUS_LED_DEFAULT_COUNT;

static uint8_t scale_channel(uint8_t channel, uint8_t brightness, float effect_scale)
{
    const uint32_t scaled = (uint32_t)channel * brightness;
    return (uint8_t)((scaled * effect_scale * s_master_brightness_percent) /
                     (255.0f * 100.0f));
}

static float effect_scale(const led_status_t *status, uint32_t now_ms)
{
    if (status->effect == LED_EFFECT_OFF) {
        return 0.0f;
    }
    if (status->effect == LED_EFFECT_SOLID || status->period_ms == 0U) {
        return 1.0f;
    }

    const uint32_t phase_ms = (now_ms + status->phase_offset_ms) % status->period_ms;
    if (status->effect == LED_EFFECT_BLINK) {
        // 上位机传入占空比时按比例点亮；0 保留系统灯原有的短脉冲默认效果。
        const uint32_t pulse_ms = status->blink_duty_percent > 0U
                                      ? ((uint32_t)status->period_ms *
                                         status->blink_duty_percent / 100U)
                                      : (status->period_ms < STATUS_BLINK_PULSE_MS
                                             ? status->period_ms
                                             : STATUS_BLINK_PULSE_MS);
        return phase_ms < pulse_ms ? 1.0f : 0.0f;
    }

    if (status->effect == LED_EFFECT_DOUBLE_BLINK) {
        // 两次短闪后保持熄灭，period_ms 表示整组双闪的重复周期。
        // 短周期会自动压缩脉冲，长周期则保持清晰的 100 ms 短闪。
        const uint32_t pulse_ms = status->period_ms / 6U < 100U
                                      ? status->period_ms / 6U : 100U;
        return (phase_ms < pulse_ms ||
                (phase_ms >= pulse_ms * 2U && phase_ms < pulse_ms * 3U))
                   ? 1.0f : 0.0f;
    }

    // 余弦曲线让呼吸的明暗转换更柔和
    const float phase = (2.0f * (float)M_PI * phase_ms) / status->period_ms;
    return 0.5f - 0.5f * cosf(phase);
}

static void render_task(void *argument)
{
    (void)argument;
    led_status_t snapshot[STATUS_LED_MAX_COUNT];
    uint8_t previous_rgb[STATUS_LED_MAX_COUNT][3] = {0};
    bool first_frame = true;
    TickType_t last_wake_tick = xTaskGetTickCount();

    while (true) {
        xSemaphoreTake(s_status_mutex, portMAX_DELAY);
        const uint8_t active_count = s_active_count;
        for (uint8_t i = 0; i < active_count; ++i) {
            snapshot[i] = s_status[i];
        }
        xSemaphoreGive(s_status_mutex);

        const uint32_t now_ms = (uint32_t)(xTaskGetTickCount() * portTICK_PERIOD_MS);
        const bool force_refresh = s_force_refresh;
        s_force_refresh = false;
        bool frame_changed = first_frame || force_refresh;
        for (uint8_t i = 0; i < active_count; ++i) {
            const float scale = effect_scale(&snapshot[i], now_ms);
            const uint8_t red = scale_channel(snapshot[i].red, snapshot[i].brightness, scale);
            const uint8_t green = scale_channel(snapshot[i].green, snapshot[i].brightness, scale);
            const uint8_t blue = scale_channel(snapshot[i].blue, snapshot[i].brightness, scale);
            if (first_frame || force_refresh || red != previous_rgb[i][0] || green != previous_rgb[i][1] ||
                blue != previous_rgb[i][2]) {
                for (uint8_t channel = 0; channel < s_channel_count; ++channel) {
                    ESP_ERROR_CHECK(led_strip_set_pixel(s_strips[channel], i, red, green, blue));
                }
                previous_rgb[i][0] = red;
                previous_rgb[i][1] = green;
                previous_rgb[i][2] = blue;
                frame_changed = true;
            }
        }
        // 常亮、熄灭以及闪烁平台期不重复启动 RMT，减少 CPU 唤醒和外设活动。
        if (frame_changed) {
            for (uint8_t channel = 0; channel < s_channel_count; ++channel) {
                ESP_ERROR_CHECK(led_strip_refresh(s_strips[channel]));
            }
            first_frame = false;
        }
        vTaskDelayUntil(&last_wake_tick, pdMS_TO_TICKS(STATUS_FRAME_MS));
    }
}

esp_err_t led_status_start(led_strip_handle_t primary_strip,
                           led_strip_handle_t secondary_strip,
                           uint8_t channel_count)
{
    ESP_RETURN_ON_FALSE(primary_strip != NULL && secondary_strip != NULL,
                        ESP_ERR_INVALID_ARG, TAG, "灯带句柄为空");
    ESP_RETURN_ON_FALSE(channel_count >= 1U && channel_count <= 2U,
                        ESP_ERR_INVALID_ARG, TAG, "灯带通道数无效");
    ESP_RETURN_ON_FALSE(s_status_mutex == NULL, ESP_ERR_INVALID_STATE, TAG, "显示任务已启动");

    s_strips[0] = primary_strip;
    s_strips[1] = secondary_strip;
    s_channel_count = channel_count;
    s_force_refresh = true;
    s_status_mutex = xSemaphoreCreateMutex();
    ESP_RETURN_ON_FALSE(s_status_mutex != NULL, ESP_ERR_NO_MEM, TAG, "无法创建状态锁");

    // 上电默认显示柔和蓝色呼吸，便于确认程序已正常运行
    for (uint8_t i = 0; i < STATUS_LED_MAX_COUNT; ++i) {
        s_status[i] = (led_status_t) {
            .red = 0,
            .green = 80,
            .blue = 255,
            .brightness = 160,
            .effect = LED_EFFECT_BREATHE,
            .period_ms = 1800,
        };
    }

    BaseType_t created = xTaskCreate(render_task, "led_renderer", 3072, NULL, 5, NULL);
    ESP_RETURN_ON_FALSE(created == pdPASS, ESP_ERR_NO_MEM, TAG, "无法创建显示任务");
    ESP_LOGI(TAG, "六灯独立状态渲染已启动");
    return ESP_OK;
}

esp_err_t led_status_set_channel_count(uint8_t channel_count)
{
    ESP_RETURN_ON_FALSE(channel_count >= 1U && channel_count <= 2U,
                        ESP_ERR_INVALID_ARG, TAG, "灯带通道数无效");
    if (s_status_mutex != NULL) {
        xSemaphoreTake(s_status_mutex, portMAX_DELAY);
    }
    const uint8_t previous = s_channel_count;
    s_channel_count = channel_count;
    if (s_status_mutex != NULL) {
        xSemaphoreGive(s_status_mutex);
    }
    if (previous == 2U && channel_count == 1U && s_strips[1] != NULL) {
        ESP_ERROR_CHECK(led_strip_clear(s_strips[1]));
    }
    return ESP_OK;
}

uint8_t led_status_get_channel_count(void)
{
    return s_channel_count;
}

esp_err_t led_status_set(uint8_t index, const led_status_t *status)
{
    ESP_RETURN_ON_FALSE(s_status_mutex != NULL, ESP_ERR_INVALID_STATE, TAG, "显示任务未启动");
    ESP_RETURN_ON_FALSE(index < s_active_count && status != NULL,
                        ESP_ERR_INVALID_ARG, TAG, "灯珠编号或状态无效");
    ESP_RETURN_ON_FALSE(status->effect <= LED_EFFECT_DOUBLE_BLINK,
                        ESP_ERR_INVALID_ARG, TAG, "显示效果无效");
    ESP_RETURN_ON_FALSE(status->blink_duty_percent <= 100U,
                        ESP_ERR_INVALID_ARG, TAG, "闪烁占空比无效");
    ESP_RETURN_ON_FALSE(status->timing_mode <= LED_ANIMATION_TIMING_MANUAL,
                        ESP_ERR_INVALID_ARG, TAG, "动画计时模式无效");

    xSemaphoreTake(s_status_mutex, portMAX_DELAY);
    s_status[index] = *status;
    xSemaphoreGive(s_status_mutex);
    return ESP_OK;
}

esp_err_t led_status_get(uint8_t index, led_status_t *status)
{
    ESP_RETURN_ON_FALSE(s_status_mutex != NULL, ESP_ERR_INVALID_STATE, TAG, "显示任务未启动");
    ESP_RETURN_ON_FALSE(index < s_active_count && status != NULL,
                        ESP_ERR_INVALID_ARG, TAG, "灯珠编号或输出指针无效");

    xSemaphoreTake(s_status_mutex, portMAX_DELAY);
    *status = s_status[index];
    xSemaphoreGive(s_status_mutex);
    return ESP_OK;
}

esp_err_t led_status_set_master_brightness(uint8_t percent)
{
    ESP_RETURN_ON_FALSE(percent <= 100U, ESP_ERR_INVALID_ARG, TAG, "全局亮度必须在 0~100 之间");
    xSemaphoreTake(s_status_mutex, portMAX_DELAY);
    s_master_brightness_percent = percent;
    xSemaphoreGive(s_status_mutex);
    return ESP_OK;
}

esp_err_t led_status_set_active_count(uint8_t count)
{
    ESP_RETURN_ON_FALSE(count >= 2U && count <= STATUS_LED_MAX_COUNT,
                        ESP_ERR_INVALID_ARG, TAG, "灯珠数量必须在 2~64 之间");
    if (s_status_mutex == NULL) {
        s_active_count = count;
        return ESP_OK;
    }
    xSemaphoreTake(s_status_mutex, portMAX_DELAY);
    s_active_count = count;
    xSemaphoreGive(s_status_mutex);
    return ESP_OK;
}

uint8_t led_status_get_active_count(void)
{
    return s_active_count;
}

esp_err_t led_status_set_progress(uint32_t completed,
                                  uint32_t total,
                                  const led_status_t *completed_status,
                                  const led_status_t *pending_status)
{
    ESP_RETURN_ON_FALSE(total > 0U && completed_status != NULL && pending_status != NULL,
                        ESP_ERR_INVALID_ARG, TAG, "进度参数无效");
    if (completed > total) {
        completed = total;
    }

    const uint8_t active_count = s_active_count;
    const uint32_t lit_count = (completed * active_count + total - 1U) / total;
    for (uint8_t i = 0; i < active_count; ++i) {
        ESP_RETURN_ON_ERROR(led_status_set(i, i < lit_count ? completed_status : pending_status),
                            TAG, "设置进度灯失败");
    }
    return ESP_OK;
}
