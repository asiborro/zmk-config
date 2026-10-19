/* SPDX-License-Identifier: MIT */
#include <zephyr/device.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <zmk/event_manager.h>
#include <zmk/events/usb_conn_state_changed.h>
#include <zmk/usb.h>
#include <zmk/workqueue.h>
#include "trackball_telemetry.h"

LOG_MODULE_REGISTER(peamk_trackball_usb, LOG_LEVEL_INF);

static const struct device *const console = DEVICE_DT_GET(DT_CHOSEN(zephyr_console));
static void report_quality(struct k_work *work);
K_WORK_DELAYABLE_DEFINE(usb_quality_work, report_quality);

static bool usb_active(void) {
    /* ZMK's HID-ready helper includes suspend, so exclude it explicitly. */
    return zmk_usb_is_hid_ready() && zmk_usb_get_status() != USB_DC_SUSPEND;
}

static void report_quality(struct k_work *work) {
    ARG_UNUSED(work);
    if (!usb_active()) {
        return;
    }

    uint32_t dtr = 0;
    if (device_is_ready(console) &&
        uart_line_ctrl_get(console, UART_LINE_CTRL_DTR, &dtr) == 0 && dtr) {
        uint8_t packet[PEAMK_TRACKBALL_PACKET_SIZE];
        char encoded[PEAMK_TRACKBALL_PACKET_SIZE * 2 + 1];
        static const char digits[] = "0123456789abcdef";
        peamk_trackball_packet(packet);
        for (size_t i = 0; i < sizeof(packet); i++) {
            encoded[2 * i] = digits[packet[i] >> 4];
            encoded[2 * i + 1] = digits[packet[i] & 15];
        }
        encoded[sizeof(encoded) - 1] = '\0';
        /* Only the diagnostic packet: no keycodes, pointer deltas or sensor I/O.
         * Device uptime lets the reader reject queued/repeated console lines. */
        LOG_INF("PEAMK_TB1 %u %s", (unsigned int)k_uptime_get_32(), encoded);
    }

    if (usb_active()) {
        k_work_reschedule_for_queue(zmk_workqueue_lowprio_work_q(), &usb_quality_work,
                                    K_SECONDS(2));
    }
}

static int usb_changed(const zmk_event_t *event) {
    if (as_zmk_usb_conn_state_changed(event)) {
        if (usb_active()) {
            k_work_reschedule_for_queue(zmk_workqueue_lowprio_work_q(), &usb_quality_work,
                                        K_SECONDS(2));
        } else {
            k_work_cancel_delayable(&usb_quality_work);
        }
    }
    return ZMK_EV_EVENT_BUBBLE;
}

ZMK_LISTENER(peamk_trackball_usb, usb_changed);
ZMK_SUBSCRIPTION(peamk_trackball_usb, zmk_usb_conn_state_changed);
