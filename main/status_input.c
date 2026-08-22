#include "status_input.h"

#include "esp_check.h"
#include "esp_log.h"

static const char *TAG = "status_input";

esp_err_t status_input_submit(const status_command_t *command)
{
    ESP_RETURN_ON_FALSE(command != NULL, ESP_ERR_INVALID_ARG, TAG, "状态命令为空");
    ESP_RETURN_ON_FALSE(command->source <= STATUS_SOURCE_BLE,
                        ESP_ERR_INVALID_ARG, TAG, "状态来源无效");
    return led_status_set(command->led_index, &command->status);
}

esp_err_t status_input_start_transports(void)
{
    /*
     * 通讯层扩展点：
     * 电脑通过 BLE GATT 服务发送状态命令，再调用 status_input_submit()。
     * 项目不再初始化 WiFi，也不保存或处理 WiFi 密码。
     */
    ESP_LOGI(TAG, "纯蓝牙状态输入框架就绪");
    return ESP_OK;
}
