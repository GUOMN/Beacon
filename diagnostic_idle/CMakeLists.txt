cmake_minimum_required(VERSION 3.16)

# Windows 环境不使用 ccache，避免编译器进程启动异常。
set(CCACHE_ENABLE 0 CACHE BOOL "Disable ccache for this diagnostic project" FORCE)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(esp32c3_idle_diagnostic)
