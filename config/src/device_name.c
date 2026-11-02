/* SPDX-License-Identifier: MIT */
/*
 * Zephyr persists the Bluetooth device name in settings ("bt/name") on first
 * boot and prefers that stored value over CONFIG_BT_DEVICE_NAME afterwards, so
 * a keyboard rename never reaches a controller that was flashed before.
 * Restore the compiled name once all settings are loaded. Static settings
 * handlers commit before ZMK's dynamically registered "ble" handler, which
 * starts advertising, so the corrected name is advertised from the first
 * packet. bt_set_name() is a no-op when the name already matches, so flash is
 * written only once after a rename.
 */
#include <string.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/logging/log.h>
#include <zephyr/settings/settings.h>

LOG_MODULE_REGISTER(keyboard_device_name, LOG_LEVEL_INF);

static int restore_device_name(void) {
    const char *stored = bt_get_name();

    if (strcmp(stored, CONFIG_BT_DEVICE_NAME) == 0) {
        return 0;
    }
    int err = bt_set_name(CONFIG_BT_DEVICE_NAME);
    if (err) {
        LOG_ERR("Failed to restore device name (err %d)", err);
        return 0;
    }
    LOG_INF("Device name restored to %s", CONFIG_BT_DEVICE_NAME);
    return 0;
}

SETTINGS_STATIC_HANDLER_DEFINE(keyboard_device_name, "keyboard_device_name", NULL, NULL,
                               restore_device_name, NULL);
