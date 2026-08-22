#pragma once

#include "esp_err.h"

// 启动长期运行的蓝牙六灯状态控制服务
esp_err_t ble_status_service_start(void);
