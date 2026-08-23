idf_component_register(SRCS "main.c"
                            "led_status.c"
                            "status_input.c"
                            "dashboard_status.c"
                            "ble_status_service.c"
                       INCLUDE_DIRS "."
                       REQUIRES bt nvs_flash app_update)
