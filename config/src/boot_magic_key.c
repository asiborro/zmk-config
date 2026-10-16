/*
 * Copyright (c) 2023 The ZMK Contributors
 *
 * SPDX-License-Identifier: MIT
 *
 * Adapted from rianadon/zmk 4fc05c26b8faa35081282d58abc84fb6bd1b52bd.
 * Use ZMK v0.3/Zephyr reset APIs in place of the fork-only zmk_reset API.
 */

#define DT_DRV_COMPAT zmk_boot_magic_key

#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/init.h>
#include <zephyr/kernel.h>
#include <zephyr/toolchain.h>
#include <zephyr/logging/log.h>
#include <zephyr/sys/reboot.h>

#include <dt-bindings/zmk/reset.h>
#include <zmk/event_manager.h>
#include <zmk/events/position_state_changed.h>

#if IS_ENABLED(CONFIG_ZMK_BLE)
#include <stdio.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/settings/settings.h>
#endif

LOG_MODULE_DECLARE(zmk, CONFIG_ZMK_LOG_LEVEL);

struct boot_key_config {
    int key_position;
    bool jump_to_bootloader;
    bool reset_settings;
};

#define BOOT_KEY_CONFIG(n)                                                                         \
    {                                                                                              \
        .key_position = DT_INST_PROP_OR(n, key_position, 0),                                       \
        .jump_to_bootloader = DT_INST_PROP_OR(n, jump_to_bootloader, false),                       \
        .reset_settings = DT_INST_PROP_OR(n, reset_settings, false),                               \
    },

static const struct boot_key_config boot_keys[] = {DT_INST_FOREACH_STATUS_OKAY(BOOT_KEY_CONFIG)};

static int64_t timeout_uptime;

static int timeout_init(void) {
    timeout_uptime = k_uptime_get() + CONFIG_ZMK_BOOT_MAGIC_KEY_TIMEOUT_MS;
    return 0;
}

SYS_INIT(timeout_init, APPLICATION, CONFIG_APPLICATION_INIT_PRIORITY);

static void reset_settings(void) {
#if IS_ENABLED(CONFIG_ZMK_BLE)
    /* Preserve the fork's zmk_ble_unpair_all() recovery behavior. */
    LOG_WRN("Clearing all existing BLE bond information from the keyboard");

    int err = bt_unpair(BT_ID_DEFAULT, NULL);
    if (err) {
        LOG_ERR("Failed to unpair default identity: %d", err);
    }

    for (int i = 0; i < 8; i++) {
        char setting_name[15];
        sprintf(setting_name, "ble/profiles/%d", i);

        int err = settings_delete(setting_name);
        if (err) {
            LOG_ERR("Failed to delete setting: %d", err);
        }
    }

    /* Also clear stored split-peripheral addresses, as in the fork. */
    for (int i = 0; i < 8; i++) {
        char setting_name[32];
        sprintf(setting_name, "ble/peripheral_addresses/%d", i);

        int err = settings_delete(setting_name);
        if (err) {
            LOG_ERR("Failed to delete setting: %d", err);
        }
    }
#endif
}

static void trigger_boot_key(const struct boot_key_config *config) {
    if (config->reset_settings) {
        LOG_INF("Boot key: resetting settings");
        reset_settings();
    }

    if (config->jump_to_bootloader) {
        LOG_INF("Boot key: jumping to bootloader");
        sys_reboot(RST_UF2);
    } else if (config->reset_settings) {
        // If resetting settings but not jumping to bootloader, we need to reboot
        // to ensure all subsystems are properly reset.
        sys_reboot(RST_WARM);
    }
}

static int event_listener(const zmk_event_t *eh) {
    if (likely(k_uptime_get() > timeout_uptime)) {
        return ZMK_EV_EVENT_BUBBLE;
    }

    const struct zmk_position_state_changed *ev = as_zmk_position_state_changed(eh);
    if (ev && ev->state) {
        for (int i = 0; i < ARRAY_SIZE(boot_keys); i++) {
            if (ev->position == boot_keys[i].key_position) {
                trigger_boot_key(&boot_keys[i]);
            }
        }
    }

    return ZMK_EV_EVENT_BUBBLE;
}

ZMK_LISTENER(boot_magic_key, event_listener);
ZMK_SUBSCRIPTION(boot_magic_key, zmk_position_state_changed);
