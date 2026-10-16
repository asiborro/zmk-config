# SPDX-License-Identifier: MIT

zephyr_library()
zephyr_library_sources_ifdef(CONFIG_ZMK_BOOT_MAGIC_KEY src/boot_magic_key.c)
zephyr_library_sources_ifdef(CONFIG_PEAMK_BATTERY_TELEMETRY src/battery_telemetry.c)
zephyr_library_include_directories(${APPLICATION_SOURCE_DIR}/include)
