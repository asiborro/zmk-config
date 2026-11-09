# SPDX-License-Identifier: MIT

if(CONFIG_PMW3610)
  # Apply on every configure, including local builds. The helper is idempotent
  # and refuses a different upstream revision or an incompatible source tree.
  set(pmw_patch_script "${CMAKE_CURRENT_LIST_DIR}/../scripts/pmw3610_patch.py")
  set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
    "${pmw_patch_script}"
    "${CMAKE_CURRENT_LIST_DIR}/../patches/pmw3610-startup-retries.patch"
    "${CMAKE_CURRENT_LIST_DIR}/../patches/pmw3610-quality-observer.patch"
    "${CMAKE_CURRENT_LIST_DIR}/../patches/pmw3610-write-clock-delay.patch"
    "${CMAKE_CURRENT_LIST_DIR}/../patches/pmw3610-split-peripheral.patch")
  execute_process(
    COMMAND "${PYTHON_EXECUTABLE}" "${pmw_patch_script}"
      "${ZEPHYR_ZMK_PMW3610_DRIVER_MODULE_DIR}"
    RESULT_VARIABLE pmw_patch_result
    OUTPUT_VARIABLE pmw_patch_output
    ERROR_VARIABLE pmw_patch_error)
  if(NOT pmw_patch_result EQUAL 0)
    message(FATAL_ERROR "PMW3610 startup patch failed: ${pmw_patch_error}")
  endif()
  string(STRIP "${pmw_patch_output}" pmw_patch_output)
  message(STATUS "${pmw_patch_output}")
endif()

if(CONFIG_PAW3395 AND CONFIG_KEYBOARD_TRACKBALL_TELEMETRY)
  set(paw_patch_script "${CMAKE_CURRENT_LIST_DIR}/../scripts/paw3395_patch.py")
  set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
    "${paw_patch_script}" "${CMAKE_CURRENT_LIST_DIR}/../patches/paw3395-quality-observer.patch")
  execute_process(COMMAND "${PYTHON_EXECUTABLE}" "${paw_patch_script}"
    "${ZEPHYR_ZMK_PAW3395_DRIVER_MODULE_DIR}"
    RESULT_VARIABLE paw_patch_result ERROR_VARIABLE paw_patch_error)
  if(NOT paw_patch_result EQUAL 0)
    message(FATAL_ERROR "PAW3395 quality patch failed: ${paw_patch_error}")
  endif()
endif()

zephyr_library()
zephyr_library_sources_ifdef(CONFIG_ZMK_BOOT_MAGIC_KEY src/boot_magic_key.c)
if(CONFIG_BT_DEVICE_NAME_DYNAMIC AND CONFIG_BT_SETTINGS)
  zephyr_library_sources(src/device_name.c)
endif()
zephyr_library_sources_ifdef(CONFIG_KEYBOARD_BATTERY_TELEMETRY src/battery_telemetry.c)
zephyr_library_sources_ifdef(CONFIG_KEYBOARD_TRACKBALL_TELEMETRY src/trackball_telemetry.c)
zephyr_library_sources_ifdef(CONFIG_KEYBOARD_TRACKBALL_USB_TELEMETRY src/trackball_usb.c)
if(CONFIG_KEYBOARD_TRACKBALL_TELEMETRY AND CONFIG_PMW3610)
  zephyr_library_include_directories("${ZEPHYR_ZMK_PMW3610_DRIVER_MODULE_DIR}/src")
endif()
if(CONFIG_KEYBOARD_TRACKBALL_TELEMETRY AND CONFIG_PAW3395)
  zephyr_library_include_directories("${ZEPHYR_ZMK_PAW3395_DRIVER_MODULE_DIR}/src")
endif()
zephyr_library_include_directories(${APPLICATION_SOURCE_DIR}/include)
