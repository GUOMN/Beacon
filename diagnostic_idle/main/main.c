#include "esp_log.h"
#include "esp_sleep.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "idle_diagnostic";

void app_main(void)
{
    /*
     * 诊断固件故意不初始化蓝牙、Wi-Fi、RMT、WS2812 或任何外设。
     * 启动后等待一秒，便于串口看到提示，随后进入无限期深度睡眠。
     * 如果此状态下 ESP32-C3 主芯片仍然明显发热，可判定为硬件故障。
     */
    ESP_LOGW(TAG, "空闲诊断固件已启动，一秒后进入深度睡眠");
    vTaskDelay(pdMS_TO_TICKS(1000));
    ESP_LOGW(TAG, "即将进入深度睡眠：无蓝牙、无 Wi-Fi、无灯带输出");
    esp_deep_sleep_start();
}
