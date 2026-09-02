#include "ble_status_service.h"

#include <string.h>
#include <stdio.h>

#include "app_config.h"
#include "dashboard_status.h"
#include "esp_check.h"
#include "esp_app_desc.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_system.h"
#include "esp_sleep.h"
#include "esp_ota_ops.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "host/ble_hs.h"
#include "host/ble_att.h"
#include "host/ble_uuid.h"
#include "led_status.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "nvs_flash.h"
#include "nvs.h"
#include "os/os_mbuf.h"
#include "services/gap/ble_svc_gap.h"
#include "services/gatt/ble_svc_gatt.h"
#include "status_input.h"

static const char *TAG = "ble_status";
static uint8_t s_own_addr_type;
static volatile bool s_connected;
static volatile bool s_ever_connected;
static volatile TickType_t s_last_data_tick;
static char s_device_name[STATUS_BLE_DEVICE_NAME_MAX];
static volatile bool s_identifying;
static volatile TickType_t s_disconnected_since_tick;
static uint16_t s_sleep_timeout_minutes = STATUS_SLEEP_TIMEOUT_DEFAULT_MIN;
static esp_ota_handle_t s_ota_handle;
static const esp_partition_t *s_ota_partition;
static size_t s_ota_expected_size;
static size_t s_ota_received_size;
static bool s_ota_active;
static bool s_v2_sequence_valid;
static uint8_t s_last_v2_sequence;

static void delayed_restart_task(void *arg)
{
    (void)arg;
    vTaskDelay(pdMS_TO_TICKS(300));
    esp_restart();
}

#define PACKET_MAGIC             0xC3U
#define PACKET_VERSION           0x01U
#define PACKET_VERSION_ORDERED   0x02U
#define PACKET_TYPE_HEARTBEAT     0x01U
#define PACKET_TYPE_SNAPSHOT      0x02U
#define PACKET_TYPE_RAW_LED       0x03U
#define PACKET_TYPE_IDENTIFY      0x04U
#define PACKET_TYPE_STATE_STYLE   0x05U
#define PACKET_TYPE_TASK_STATE    0x06U
#define PACKET_TYPE_PANEL_HEADER  0x07U
#define PACKET_TYPE_LED_COUNT     0x08U
#define PACKET_TYPE_SLEEP_TIMEOUT 0x09U
#define PACKET_TYPE_CHANNEL_COUNT  0x0AU
#define PACKET_TYPE_SYSTEM_EFFECT  0x0BU
#define PACKET_TYPE_SYSTEM_BRIGHTNESS 0x0CU
#define HEARTBEAT_PACKET_SIZE     4U
#define SNAPSHOT_PACKET_SIZE      17U
#define RAW_LED_PACKET_SIZE       12U
#define IDENTIFY_PACKET_SIZE      4U
#define STATE_STYLE_PACKET_SIZE_LEGACY   9U
#define STATE_STYLE_PACKET_SIZE_EXTENDED 12U
#define TASK_STATE_PACKET_SIZE_LEGACY 7U
#define TASK_STATE_PACKET_SIZE_EXTENDED 9U
#define TASK_STATE_PACKET_SIZE_TIMING 10U
#define PANEL_HEADER_PACKET_SIZE  7U
#define LED_COUNT_PACKET_SIZE     5U
#define SLEEP_TIMEOUT_PACKET_SIZE 6U
#define CHANNEL_COUNT_PACKET_SIZE 5U
#define SYSTEM_EFFECT_PACKET_SIZE 5U
#define SYSTEM_BRIGHTNESS_PACKET_SIZE 5U

static esp_err_t save_sleep_timeout(uint16_t minutes)
{
    nvs_handle_t handle;
    ESP_RETURN_ON_ERROR(nvs_open("status_panel", NVS_READWRITE, &handle), TAG,
                        "打开休眠设置失败");
    esp_err_t result = nvs_set_u16(handle, "sleep_min", minutes);
    if (result == ESP_OK) {
        result = nvs_commit(handle);
    }
    nvs_close(handle);
    return result;
}

static void enter_deep_sleep(void)
{
    ESP_LOGI(TAG, "蓝牙已断连 %u 分钟，关闭灯光并进入深度睡眠",
             s_sleep_timeout_minutes);
    const led_status_t off = {.effect = LED_EFFECT_OFF};
    for (uint8_t i = 0; i < led_status_get_active_count(); ++i) {
        ESP_ERROR_CHECK(led_status_set(i, &off));
    }
    vTaskDelay(pdMS_TO_TICKS(STATUS_FRAME_MS * 2U));
    esp_deep_sleep_start();
}

// 认领时暂存原灯效，播放白色流水后原样恢复。
static void identify_task(void *arg)
{
    (void)arg;
    const uint8_t active_count = led_status_get_active_count();
    led_status_t saved[STATUS_LED_MAX_COUNT];
    for (uint8_t i = 0; i < active_count; ++i) {
        ESP_ERROR_CHECK(led_status_get(i, &saved[i]));
        const led_status_t identify = {
            .red = 255,
            .green = 255,
            .blue = 255,
            .brightness = 255,
            .effect = LED_EFFECT_BREATHE,
            .period_ms = 900,
            .phase_offset_ms = (uint16_t)i * 150U,
        };
        ESP_ERROR_CHECK(led_status_set(i, &identify));
    }

    vTaskDelay(pdMS_TO_TICKS(3000));
    for (uint8_t i = 0; i < active_count; ++i) {
        ESP_ERROR_CHECK(led_status_set(i, &saved[i]));
    }
    s_identifying = false;
    vTaskDelete(NULL);
}

/*
 * 自定义蓝牙协议 UUID。协议共有三种短消息，均小于默认 BLE MTU：
 * 1. 心跳：4 字节，用来证明 Codex 插件仍在运行。
 * 2. 面板快照：14 字节，一次更新用量灯和四颗任务灯。
 * 3. 原始单灯：12 字节，调试时直接设置任意一颗灯的 RGB 与效果。
 */
static const ble_uuid128_t STATUS_SERVICE_UUID = BLE_UUID128_INIT(
    0x6a, 0x7d, 0x17, 0xe4, 0xb8, 0x32, 0x4c, 0x93,
    0x9e, 0x81, 0x25, 0x76, 0x10, 0xc3, 0x00, 0x01);
static const ble_uuid128_t STATUS_CONTROL_UUID = BLE_UUID128_INIT(
    0x6a, 0x7d, 0x17, 0xe4, 0xb8, 0x32, 0x4c, 0x93,
    0x9e, 0x81, 0x25, 0x76, 0x10, 0xc3, 0x00, 0x02);
static const ble_uuid128_t STATUS_OTA_UUID = BLE_UUID128_INIT(
    0x6a, 0x7d, 0x17, 0xe4, 0xb8, 0x32, 0x4c, 0x93,
    0x9e, 0x81, 0x25, 0x76, 0x10, 0xc3, 0x00, 0x03);
static const ble_uuid128_t STATUS_INFO_UUID = BLE_UUID128_INIT(
    0x6a, 0x7d, 0x17, 0xe4, 0xb8, 0x32, 0x4c, 0x93,
    0x9e, 0x81, 0x25, 0x76, 0x10, 0xc3, 0x00, 0x04);

static int firmware_info_access(uint16_t conn_handle, uint16_t attr_handle,
                                struct ble_gatt_access_ctxt *ctxt, void *arg)
{
    (void)conn_handle;
    (void)attr_handle;
    (void)arg;
    if (ctxt->op != BLE_GATT_ACCESS_OP_READ_CHR) {
        return BLE_ATT_ERR_READ_NOT_PERMITTED;
    }
    const esp_app_desc_t *description = esp_app_get_description();
    const esp_partition_t *running = esp_ota_get_running_partition();
    char info[80];
    const int length = snprintf(info, sizeof(info), "%s|%s|%s %s",
                                description->version,
                                running != NULL ? running->label : "unknown",
                                description->date, description->time);
    return os_mbuf_append(ctxt->om, info, (uint16_t)length) == 0 ? 0 : BLE_ATT_ERR_INSUFFICIENT_RES;
}

enum {
    OTA_COMMAND_START = 0x01,
    OTA_COMMAND_DATA = 0x02,
    OTA_COMMAND_FINISH = 0x03,
    OTA_COMMAND_ABORT = 0x04,
};

static void ota_abort(void)
{
    if (s_ota_active) {
        esp_ota_abort(s_ota_handle);
    }
    s_ota_active = false;
    s_ota_partition = NULL;
    s_ota_expected_size = 0;
    s_ota_received_size = 0;
}

static int ota_access(uint16_t conn_handle, uint16_t attr_handle,
                      struct ble_gatt_access_ctxt *ctxt, void *arg)
{
    (void)conn_handle;
    (void)attr_handle;
    (void)arg;
    if (ctxt->op != BLE_GATT_ACCESS_OP_WRITE_CHR) {
        return BLE_ATT_ERR_WRITE_NOT_PERMITTED;
    }
    const uint16_t packet_len = OS_MBUF_PKTLEN(ctxt->om);
    uint8_t packet[512];
    if (packet_len < 1U || packet_len > sizeof(packet) ||
        ble_hs_mbuf_to_flat(ctxt->om, packet, packet_len, NULL) != 0) {
        return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
    }

    esp_err_t result = ESP_ERR_INVALID_ARG;
    switch (packet[0]) {
    case OTA_COMMAND_START:
        if (packet_len == 5U) {
            ota_abort();
            s_ota_expected_size = (size_t)packet[1] | ((size_t)packet[2] << 8) |
                                  ((size_t)packet[3] << 16) | ((size_t)packet[4] << 24);
            s_ota_partition = esp_ota_get_next_update_partition(NULL);
            if (s_ota_partition != NULL && s_ota_expected_size > 0U &&
                s_ota_expected_size <= s_ota_partition->size) {
                result = esp_ota_begin(s_ota_partition, s_ota_expected_size, &s_ota_handle);
                if (result == ESP_OK) {
                    s_ota_active = true;
                    ESP_LOGI(TAG, "开始蓝牙 OTA：%u 字节写入 %s",
                             (unsigned)s_ota_expected_size, s_ota_partition->label);
                }
            }
        }
        break;
    case OTA_COMMAND_DATA:
        if (s_ota_active && packet_len > 1U &&
            s_ota_received_size + packet_len - 1U <= s_ota_expected_size) {
            result = esp_ota_write(s_ota_handle, &packet[1], packet_len - 1U);
            if (result == ESP_OK) {
                s_ota_received_size += packet_len - 1U;
            }
        }
        break;
    case OTA_COMMAND_FINISH:
        if (packet_len == 1U && s_ota_active &&
            s_ota_received_size == s_ota_expected_size) {
            result = esp_ota_end(s_ota_handle);
            s_ota_active = false;
            if (result == ESP_OK) {
                result = esp_ota_set_boot_partition(s_ota_partition);
            }
            if (result == ESP_OK) {
                ESP_LOGI(TAG, "蓝牙 OTA 校验通过，即将切换到新固件");
                xTaskCreate(delayed_restart_task, "ota_restart", 2048, NULL, 5, NULL);
            } else {
                ota_abort();
            }
        }
        break;
    case OTA_COMMAND_ABORT:
        ota_abort();
        result = ESP_OK;
        break;
    default:
        break;
    }
    return result == ESP_OK ? 0 : BLE_ATT_ERR_VALUE_NOT_ALLOWED;
}

static int control_access(uint16_t conn_handle, uint16_t attr_handle,
                          struct ble_gatt_access_ctxt *ctxt, void *arg)
{
    (void)conn_handle;
    (void)attr_handle;
    (void)arg;

    if (ctxt->op != BLE_GATT_ACCESS_OP_WRITE_CHR) {
        return BLE_ATT_ERR_WRITE_NOT_PERMITTED;
    }

    const uint16_t packet_len = OS_MBUF_PKTLEN(ctxt->om);
    uint8_t packet[SNAPSHOT_PACKET_SIZE];
    if (packet_len > sizeof(packet) || packet_len < HEARTBEAT_PACKET_SIZE ||
        ble_hs_mbuf_to_flat(ctxt->om, packet, packet_len, NULL) != 0) {
        return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
    }

    if (packet[0] != PACKET_MAGIC ||
        (packet[1] != PACKET_VERSION && packet[1] != PACKET_VERSION_ORDERED)) {
        return BLE_ATT_ERR_VALUE_NOT_ALLOWED;
    }
    const bool ordered_packet = packet[1] == PACKET_VERSION_ORDERED;
    if (ordered_packet && s_v2_sequence_valid) {
        const uint8_t distance = (uint8_t)(packet[3] - s_last_v2_sequence);
        if (distance == 0U || distance >= 128U) {
            ESP_LOGW(TAG, "丢弃旧控制包 seq=%u last=%u", packet[3], s_last_v2_sequence);
            return BLE_ATT_ERR_VALUE_NOT_ALLOWED;
        }
    }

    esp_err_t result = ESP_ERR_INVALID_ARG;
    switch (packet[2]) {
    case PACKET_TYPE_HEARTBEAT:
        if (packet_len == HEARTBEAT_PACKET_SIZE) {
            result = ESP_OK;
        }
        break;
    case PACKET_TYPE_SNAPSHOT:
        if (packet_len == SNAPSHOT_PACKET_SIZE) {
            // 4=剩余量，5=短周期已用量，6~10=任务状态，11~15=任务进度，16=全局亮度。
            result = dashboard_status_set_snapshot(
                packet[4], packet[5], &packet[6], &packet[11]);
            if (result == ESP_OK) {
                result = led_status_set_master_brightness(packet[16]);
            }
        }
        break;
    case PACKET_TYPE_RAW_LED:
        if (packet_len == RAW_LED_PACKET_SIZE) {
            const status_command_t command = {
                .source = STATUS_SOURCE_BLE,
                .led_index = packet[4],
                .status = {
                    .red = packet[5],
                    .green = packet[6],
                    .blue = packet[7],
                    .brightness = packet[8],
                    .effect = (led_effect_t)packet[9],
                    .period_ms = (uint16_t)packet[10] | ((uint16_t)packet[11] << 8),
                },
            };
            result = status_input_submit(&command);
        }
        break;
    case PACKET_TYPE_IDENTIFY:
        if (packet_len == IDENTIFY_PACKET_SIZE) {
            result = ESP_OK;
            if (!s_identifying) {
                s_identifying = true;
                if (xTaskCreate(identify_task, "ble_identify", 3072, NULL, 5, NULL) != pdPASS) {
                    s_identifying = false;
                    result = ESP_ERR_NO_MEM;
                }
            }
        }
        break;
    case PACKET_TYPE_STATE_STYLE:
        if (packet_len == STATE_STYLE_PACKET_SIZE_LEGACY ||
            packet_len == STATE_STYLE_PACKET_SIZE_EXTENDED) {
            const uint16_t period_ms = packet_len == STATE_STYLE_PACKET_SIZE_EXTENDED
                                           ? ((uint16_t)packet[9] | ((uint16_t)packet[10] << 8))
                                           : 1200U;
            const uint8_t duty_percent = packet_len == STATE_STYLE_PACKET_SIZE_EXTENDED
                                             ? packet[11] : 15U;
            result = dashboard_status_set_state_style(
                (panel_state_t)packet[4], packet[5], packet[6], packet[7],
                (led_effect_t)packet[8], period_ms, duty_percent);
        }
        break;
    case PACKET_TYPE_TASK_STATE:
        if ((packet_len == TASK_STATE_PACKET_SIZE_LEGACY ||
             packet_len == TASK_STATE_PACKET_SIZE_EXTENDED ||
             packet_len == TASK_STATE_PACKET_SIZE_TIMING) &&
            packet[4] < led_status_get_active_count() - 1U) {
            const uint16_t period_ms = packet_len >= TASK_STATE_PACKET_SIZE_EXTENDED
                                           ? ((uint16_t)packet[7] | ((uint16_t)packet[8] << 8))
                                           : 0U;
            // 新包 byte 9：0=自动逐任务频率，1=手动状态级频率。
            // 旧 9 字节包带显式周期，继续按手动模式解释，保持兼容。
            const led_animation_timing_t timing_mode =
                packet_len == TASK_STATE_PACKET_SIZE_TIMING
                    ? (led_animation_timing_t)packet[9]
                    : (period_ms == 0U ? LED_ANIMATION_TIMING_AUTO
                                       : LED_ANIMATION_TIMING_MANUAL);
            result = dashboard_status_set_with_timing(
                (uint8_t)(packet[4] + 1U), (panel_state_t)packet[5], packet[6],
                period_ms, timing_mode);
        }
        break;
    case PACKET_TYPE_LED_COUNT:
        if (packet_len == LED_COUNT_PACKET_SIZE) {
            const uint8_t old_count = led_status_get_active_count();
            result = packet[4] >= 2U && packet[4] <= STATUS_LED_MAX_COUNT
                         ? ESP_OK : ESP_ERR_INVALID_ARG;
            if (result == ESP_OK) {
                nvs_handle_t handle;
                result = nvs_open("status_panel", NVS_READWRITE, &handle);
                if (result == ESP_OK) {
                    result = nvs_set_u8(handle, "led_count", packet[4]);
                    if (result == ESP_OK) {
                        result = nvs_commit(handle);
                    }
                    nvs_close(handle);
                }
                if (result == ESP_OK && packet[4] != old_count) {
                    // 灯带驱动长度需在启动时创建；保存后延迟重启一次即可生效。
                    if (xTaskCreate(delayed_restart_task, "led_count_restart", 2048,
                                    NULL, 5, NULL) != pdPASS) {
                        result = ESP_ERR_NO_MEM;
                    }
                }
            }
        }
        break;
    case PACKET_TYPE_SLEEP_TIMEOUT:
        if (packet_len == SLEEP_TIMEOUT_PACKET_SIZE) {
            const uint16_t minutes = (uint16_t)packet[4] | ((uint16_t)packet[5] << 8);
            result = minutes >= 1U && minutes <= STATUS_SLEEP_TIMEOUT_MAX_MIN
                         ? save_sleep_timeout(minutes)
                         : ESP_ERR_INVALID_ARG;
            if (result == ESP_OK) {
                s_sleep_timeout_minutes = minutes;
                ESP_LOGI(TAG, "断连休眠等待时间已更新为 %u 分钟", minutes);
            }
        }
        break;
    case PACKET_TYPE_CHANNEL_COUNT:
        if (packet_len == CHANNEL_COUNT_PACKET_SIZE && packet[4] >= 1U && packet[4] <= 2U) {
            const uint8_t old_channel_count = led_status_get_channel_count();
            nvs_handle_t handle;
            result = nvs_open("status_panel", NVS_READWRITE, &handle);
            if (result == ESP_OK) {
                result = nvs_set_u8(handle, "channels", packet[4]);
                if (result == ESP_OK) {
                    result = nvs_commit(handle);
                }
                nvs_close(handle);
            }
            if (result == ESP_OK && packet[4] != old_channel_count) {
                // RMT 内存和通道句柄只能在启动阶段安全重配，保存后重启生效。
                ESP_LOGI(TAG, "灯带输出通道将从 %u 路切换为 %u 路，正在重启",
                         old_channel_count, packet[4]);
                if (xTaskCreate(delayed_restart_task, "channel_count_restart", 2048,
                                NULL, 5, NULL) != pdPASS) {
                    result = ESP_ERR_NO_MEM;
                }
            }
        } else {
            result = ESP_ERR_INVALID_ARG;
        }
        break;
    case PACKET_TYPE_SYSTEM_EFFECT:
        if (packet_len == SYSTEM_EFFECT_PACKET_SIZE) {
            result = dashboard_status_set_system_effect((led_effect_t)packet[4]);
        }
        break;
    case PACKET_TYPE_SYSTEM_BRIGHTNESS:
        if (packet_len == SYSTEM_BRIGHTNESS_PACKET_SIZE) {
            result = dashboard_status_set_system_brightness(packet[4]);
        }
        break;
    case PACKET_TYPE_PANEL_HEADER:
        if (packet_len == PANEL_HEADER_PACKET_SIZE) {
            result = dashboard_status_set_usage(packet[4], packet[5]);
            if (result == ESP_OK) {
                result = led_status_set_master_brightness(packet[6]);
            }
        }
        break;
    default:
        break;
    }

    ESP_LOGI(TAG, "控制包 type=0x%02x seq=%u len=%u result=%s",
             packet[2], packet[3], packet_len, esp_err_to_name(result));

    if (result != ESP_OK) {
        return BLE_ATT_ERR_VALUE_NOT_ALLOWED;
    }
    if (ordered_packet) {
        s_last_v2_sequence = packet[3];
        s_v2_sequence_valid = true;
    }

    s_last_data_tick = xTaskGetTickCount();
    ESP_ERROR_CHECK(dashboard_status_set_connection(true, true, false));
    return 0;
}

static const struct ble_gatt_svc_def s_services[] = {
    {
        .type = BLE_GATT_SVC_TYPE_PRIMARY,
        .uuid = &STATUS_SERVICE_UUID.u,
        .characteristics = (struct ble_gatt_chr_def[]) {
            {
                .uuid = &STATUS_CONTROL_UUID.u,
                .access_cb = control_access,
                .flags = BLE_GATT_CHR_F_WRITE | BLE_GATT_CHR_F_WRITE_NO_RSP,
            },
            {
                .uuid = &STATUS_OTA_UUID.u,
                .access_cb = ota_access,
                .flags = BLE_GATT_CHR_F_WRITE | BLE_GATT_CHR_F_WRITE_NO_RSP,
            },
            {
                .uuid = &STATUS_INFO_UUID.u,
                .access_cb = firmware_info_access,
                .flags = BLE_GATT_CHR_F_READ,
            },
            {0},
        },
    },
    {0},
};

static int gap_event(struct ble_gap_event *event, void *arg);

static void start_advertising(void)
{
    struct ble_hs_adv_fields fields = {0};
    fields.flags = BLE_HS_ADV_F_DISC_GEN | BLE_HS_ADV_F_BREDR_UNSUP;
    fields.uuids128 = (ble_uuid128_t *)&STATUS_SERVICE_UUID;
    fields.num_uuids128 = 1;
    fields.uuids128_is_complete = 1;
    if (ble_gap_adv_set_fields(&fields) != 0) {
        ESP_LOGE(TAG, "设置蓝牙广播数据失败");
        return;
    }

    // 设备名放入扫描响应，避免名称与 128 位 UUID 超出广播包容量。
    struct ble_hs_adv_fields response = {0};
    response.name = (uint8_t *)s_device_name;
    response.name_len = strlen(s_device_name);
    response.name_is_complete = 1;
    if (ble_gap_adv_rsp_set_fields(&response) != 0) {
        ESP_LOGE(TAG, "设置蓝牙扫描响应失败");
        return;
    }

    const struct ble_gap_adv_params params = {
        .conn_mode = BLE_GAP_CONN_MODE_UND,
        .disc_mode = BLE_GAP_DISC_MODE_GEN,
        // 100~150 ms 足以让桌面端快速发现，同时降低断连等待期间的射频占空比。
        .itvl_min = BLE_GAP_ADV_ITVL_MS(100),
        .itvl_max = BLE_GAP_ADV_ITVL_MS(150),
    };
    const int rc = ble_gap_adv_start(s_own_addr_type, NULL, BLE_HS_FOREVER,
                                     &params, gap_event, NULL);
    if (rc != 0) {
        ESP_LOGE(TAG, "启动蓝牙广播失败：%d", rc);
    }
}

static int gap_event(struct ble_gap_event *event, void *arg)
{
    (void)arg;
    switch (event->type) {
    case BLE_GAP_EVENT_CONNECT:
        if (event->connect.status == 0) {
            s_connected = true;
            s_v2_sequence_valid = false;
            s_ever_connected = true;
            s_last_data_tick = xTaskGetTickCount();
            s_disconnected_since_tick = 0;
            // 配对成功后熄灭等待动画，六颗灯全部留给后续业务快照。
            for (uint8_t i = 0; i < led_status_get_active_count(); ++i) {
                ESP_ERROR_CHECK(dashboard_status_set(i, PANEL_STATE_IDLE, 0));
            }
            ESP_LOGI(TAG, "电脑已连接蓝牙状态服务");
        } else {
            start_advertising();
        }
        break;
    case BLE_GAP_EVENT_DISCONNECT:
        s_connected = false;
        s_v2_sequence_valid = false;
        s_disconnected_since_tick = xTaskGetTickCount();
        ESP_LOGI(TAG, "蓝牙已断开，重新等待连接");
        ESP_ERROR_CHECK(dashboard_status_set_connection(false, false, true));
        start_advertising();
        break;
    case BLE_GAP_EVENT_ADV_COMPLETE:
        start_advertising();
        break;
    default:
        break;
    }
    return 0;
}

static void host_sync(void)
{
    if (ble_hs_id_infer_auto(0, &s_own_addr_type) != 0) {
        ESP_LOGE(TAG, "无法确定蓝牙地址类型");
        return;
    }
    ESP_ERROR_CHECK(dashboard_status_set_connection(false, false, false));
    s_disconnected_since_tick = xTaskGetTickCount();
    start_advertising();
    ESP_LOGI(TAG, "纯蓝牙状态服务已启动：%s", s_device_name);
}

static void host_task(void *arg)
{
    (void)arg;
    nimble_port_run();
    nimble_port_freertos_deinit();
}

static void health_task(void *arg)
{
    (void)arg;
    bool was_alive = false;

    while (true) {
        bool alive = false;
        if (s_connected) {
            const TickType_t elapsed = xTaskGetTickCount() - s_last_data_tick;
            alive = elapsed <= pdMS_TO_TICKS(STATUS_DATA_TIMEOUT_MS);
        }
        if (alive != was_alive) {
            ESP_ERROR_CHECK(dashboard_status_set_connection(
                s_connected, alive, s_ever_connected && !alive));
            was_alive = alive;
        }
        if (!s_connected && s_disconnected_since_tick != 0U) {
            const TickType_t disconnected_ticks = xTaskGetTickCount() - s_disconnected_since_tick;
            const uint64_t timeout_ms = (uint64_t)s_sleep_timeout_minutes * 60ULL * 1000ULL;
            if ((uint64_t)disconnected_ticks * portTICK_PERIOD_MS >= timeout_ms) {
                enter_deep_sleep();
            }
        }
        vTaskDelay(pdMS_TO_TICKS(STATUS_HEALTH_CHECK_MS));
    }
}

esp_err_t ble_status_service_start(void)
{
    // 使用芯片出厂 MAC 的后 3 字节生成 6 位唯一短 ID。
    // 该 ID 不依赖 Windows 分配的蓝牙地址，重启和重新烧录都不会改变。
    uint8_t base_mac[6];
    ESP_RETURN_ON_ERROR(esp_read_mac(base_mac, ESP_MAC_BASE), TAG, "读取芯片唯一 MAC 失败");
    snprintf(s_device_name, sizeof(s_device_name), "%s%02X%02X%02X",
             STATUS_BLE_DEVICE_NAME_PREFIX, base_mac[3], base_mac[4], base_mac[5]);

    // NimBLE 需要 NVS 保存射频校准和绑定信息，但不会自动擦除 NVS。
    ESP_RETURN_ON_ERROR(nvs_flash_init(), TAG, "NVS 初始化失败");
    nvs_handle_t settings_handle;
    if (nvs_open("status_panel", NVS_READONLY, &settings_handle) == ESP_OK) {
        uint16_t saved_minutes = STATUS_SLEEP_TIMEOUT_DEFAULT_MIN;
        if (nvs_get_u16(settings_handle, "sleep_min", &saved_minutes) == ESP_OK &&
            saved_minutes >= 1U && saved_minutes <= STATUS_SLEEP_TIMEOUT_MAX_MIN) {
            s_sleep_timeout_minutes = saved_minutes;
        }
        nvs_close(settings_handle);
    }

    ESP_RETURN_ON_ERROR(nimble_port_init(), TAG, "NimBLE 初始化失败");
    ESP_RETURN_ON_ERROR(ble_att_set_preferred_mtu(247), TAG, "设置 OTA 蓝牙 MTU 失败");

    ble_svc_gap_init();
    ble_svc_gatt_init();
    ESP_RETURN_ON_FALSE(ble_gatts_count_cfg(s_services) == 0,
                        ESP_FAIL, TAG, "统计 GATT 服务失败");
    ESP_RETURN_ON_FALSE(ble_gatts_add_svcs(s_services) == 0,
                        ESP_FAIL, TAG, "注册 GATT 服务失败");
    ESP_RETURN_ON_FALSE(ble_svc_gap_device_name_set(s_device_name) == 0,
                        ESP_FAIL, TAG, "设置蓝牙名称失败");

    ble_hs_cfg.sync_cb = host_sync;
    nimble_port_freertos_init(host_task);
    ESP_RETURN_ON_FALSE(xTaskCreate(health_task, "ble_health", 2048, NULL, 4, NULL) == pdPASS,
                        ESP_ERR_NO_MEM, TAG, "创建蓝牙健康检测任务失败");
    return ESP_OK;
}
