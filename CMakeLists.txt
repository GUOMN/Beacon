cmake_minimum_required(VERSION 3.16)

# 关闭 ccache：当前 Windows 环境中它无法启动 ESP32-C3 编译器。
# 工程内固定关闭后，VS Code 和 idf.py 会使用编译器本体。
set(CCACHE_ENABLE 0 CACHE BOOL "Disable ccache for this project" FORCE)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(ws2812_status)
