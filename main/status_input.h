#pragma once

#include <stdint.h>

#include "esp_err.h"
#include "led_status.h"

// 状态数据来源。后续可据此做优先级、超时和日志处理。
typedef enum {
    STATUS_SOURCE_LOCAL = 0,
    STATUS_SOURCE_BLE,
} status_source_t;

typedef struct {
    status_source_t source;
    uint8_t led_index;
    led_status_t status;
} status_command_t;

// 所有通讯方式最终都调用这个统一入口更新灯珠
esp_err_t status_input_submit(const status_command_t *command);

// 初始化纯蓝牙状态输入层
esp_err_t status_input_start_transports(void);
