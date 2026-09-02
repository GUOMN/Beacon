#include "app_config.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "led_status.h"
#include "status_input.h"
#include "ble_status_service.h"
#include "dashboard_status.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "esp_ota_ops.h"

static const char *TAG = "status_panel";

void app_main(void)
{
    /* 上电时先读取上位机保存的灯珠数，首次启动默认 6 颗。 */
    ESP_ERROR_CHECK(nvs_flash_init());
    ESP_ERROR_CHECK(dashboard_status_load_saved_styles());
    nvs_handle_t count_handle;
    uint8_t saved_channels = 1U;
    if (nvs_open("status_panel", NVS_READONLY, &count_handle) == ESP_OK) {
        uint8_t saved_count = STATUS_LED_DEFAULT_COUNT;
        if (nvs_get_u8(count_handle, "led_count", &saved_count) == ESP_OK &&
            saved_count >= 2U && saved_count <= STATUS_LED_MAX_COUNT) {
            ESP_ERROR_CHECK(led_status_set_active_count(saved_count));
        }
        if (nvs_get_u8(count_handle, "channels", &saved_channels) != ESP_OK ||
            saved_channels < 1U || saved_channels > 2U) {
            saved_channels = 1U;
        }
        nvs_close(count_handle);
    }

    /* 驱动只按实际灯珠数分配和发送，避免预留 64 颗导致无效运算与发热。 */
    led_strip_config_t strip_config = {
        .strip_gpio_num = STATUS_DATA_GPIO,
        .max_leds = led_status_get_active_count(),
        .led_model = LED_MODEL_WS2812,
        .color_component_format = LED_STRIP_COLOR_COMPONENT_FMT_GRB,
        .flags.invert_out = false,
    };

    /* ESP32-C3 使用 RMT 外设生成 WS2812 所需的精确时序。 */
    led_strip_rmt_config_t rmt_config = {
        .clk_src = RMT_CLK_SRC_DEFAULT,
        .resolution_hz = STATUS_RMT_RESOLUTION_HZ,
        .mem_block_symbols = saved_channels == 1U
                                 ? STATUS_RMT_SINGLE_CHANNEL_MEMORY_SYMBOLS
                                 : STATUS_RMT_DUAL_CHANNEL_MEMORY_SYMBOLS,
        .flags.with_dma = false,
    };

    led_strip_handle_t strip = NULL;
    led_strip_handle_t secondary_strip = NULL;
    ESP_ERROR_CHECK(led_strip_new_rmt_device(&strip_config, &rmt_config, &strip));
    ESP_ERROR_CHECK(led_strip_clear(strip));
    if (saved_channels == 2U) {
        strip_config.strip_gpio_num = STATUS_SECONDARY_DATA_GPIO;
        ESP_ERROR_CHECK(led_strip_new_rmt_device(&strip_config, &rmt_config,
                                                 &secondary_strip));
        ESP_ERROR_CHECK(led_strip_clear(secondary_strip));
    }

    ESP_LOGI(TAG, "RMT 已按 %u 路输出初始化，每路 %u symbols", saved_channels,
             (unsigned int)rmt_config.mem_block_symbols);

    /*
     * 上电硬件自检：绕过蓝牙与状态逻辑，直接把六颗灯点为低亮度白色。
     * 如果这一段仍不亮，就能确认问题在数据引脚、电平、供电或灯带型号。
     */
    for (uint8_t i = 0; i < led_status_get_active_count(); ++i) {
        ESP_ERROR_CHECK(led_strip_set_pixel(strip, i, 32, 32, 32));
        if (saved_channels == 2U) {
            ESP_ERROR_CHECK(led_strip_set_pixel(secondary_strip, i, 32, 32, 32));
        }
    }
    ESP_ERROR_CHECK(led_strip_refresh(strip));
    if (saved_channels == 2U) {
        ESP_ERROR_CHECK(led_strip_refresh(secondary_strip));
    }
    vTaskDelay(pdMS_TO_TICKS(STATUS_POWER_ON_TEST_MS));
    ESP_ERROR_CHECK(led_strip_clear(strip));
    if (secondary_strip != NULL) {
        ESP_ERROR_CHECK(led_strip_clear(secondary_strip));
    }

    ESP_ERROR_CHECK(led_status_start(strip, secondary_strip, saved_channels));
    ESP_ERROR_CHECK(status_input_start_transports());
    ESP_ERROR_CHECK(ble_status_service_start());

    /* 只有灯带、输入和蓝牙全部初始化成功后才确认 OTA 镜像。
     * 如果新镜像在此之前启动失败，下次重启仍可由 bootloader 回滚。 */
    ESP_ERROR_CHECK(esp_ota_mark_app_valid_cancel_rollback());

    ESP_LOGI(TAG, "六灯纯蓝牙状态面板就绪：GPIO %d", STATUS_DATA_GPIO);
}
