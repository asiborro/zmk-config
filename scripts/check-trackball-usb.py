"""Compile the production USB reporter and check connection/console gating."""
from pathlib import Path
import subprocess
import tempfile

from trackball_usb import parse_line

STUBS = r'''
#pragma once
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define ARG_UNUSED(v) (void)(v)
#define LOG_LEVEL_INF 3
#define LOG_MODULE_REGISTER(...)
#define LOG_INF(...) do { emissions++; snprintf(last_line, sizeof(last_line), __VA_ARGS__); } while (0)
#define DT_CHOSEN(n) 0
#define DEVICE_DT_GET(n) (&test_device)
#define UART_LINE_CTRL_DTR 1
#define USB_DC_SUSPEND 9
#define ZMK_EV_EVENT_BUBBLE 0
#define K_SECONDS(n) ((n)*1000)
#define ZMK_LISTENER(...)
#define ZMK_SUBSCRIPTION(...)
struct device { int unused; };
struct k_work { void (*handler)(struct k_work *); };
struct k_work_delayable { struct k_work work; };
#define K_WORK_DELAYABLE_DEFINE(n, cb) static struct k_work_delayable n = { .work = { .handler = cb } }
typedef int zmk_event_t;
struct zmk_usb_conn_state_changed { int unused; };
static struct device test_device;
static struct zmk_usb_conn_state_changed test_event;
static bool hid_ready, device_ready = true, unplug_during_copy;
static uint32_t dtr, now = 1234;
static int status, line_error, copies, emissions, schedules, cancels;
static char last_line[128];
static bool zmk_usb_is_hid_ready(void) { return hid_ready; }
static int zmk_usb_get_status(void) { return status; }
static bool device_is_ready(const struct device *dev) { (void)dev; return device_ready; }
static int uart_line_ctrl_get(const struct device *dev, int ctrl, uint32_t *value) {
    (void)dev; assert(ctrl == UART_LINE_CTRL_DTR); *value = dtr; return line_error;
}
static uint32_t k_uptime_get_32(void) { return now; }
static void *zmk_workqueue_lowprio_work_q(void) { return &test_device; }
static int k_work_reschedule_for_queue(void *queue, struct k_work_delayable *work, int delay) {
    (void)queue; (void)work; assert(delay==2000); schedules++; return 0;
}
static int k_work_cancel_delayable(struct k_work_delayable *work) { (void)work; cancels++; return 0; }
static const struct zmk_usb_conn_state_changed *as_zmk_usb_conn_state_changed(const zmk_event_t *event) {
    return *event ? &test_event : NULL;
}
'''
HARNESS = r'''
#include "stubs.h"
#include "trackball_usb.c"
void peamk_trackball_packet(uint8_t packet[20]) {
    static const uint8_t sample[20] = {1,7,110,0,224,1,0,0,0,0,42,0,0,0,0,0,0,0,4,0};
    memcpy(packet,sample,20); copies++;
    if (unplug_during_copy) hid_ready = false;
}
int main(void) {
    zmk_event_t event=1, unrelated=0;
    usb_changed(&event);
    report_quality(NULL);
    assert(cancels==1 && schedules==0 && copies==0);
    hid_ready=true;
    usb_changed(&event);
    assert(schedules==1);
    report_quality(NULL); /* No open console: no snapshots or log lines. */
    assert(schedules==2 && emissions==0 && copies==0);
    dtr=1; line_error=-5;
    report_quality(NULL);
    assert(schedules==3 && copies==0);
    line_error=0; device_ready=false;
    report_quality(NULL);
    assert(schedules==4 && copies==0);
    device_ready=true;
    report_quality(NULL);
    assert(schedules==5 && copies==1 && emissions==1);
    puts(last_line);
    status=USB_DC_SUSPEND;
    usb_changed(&event);
    report_quality(NULL);
    assert(cancels==2 && schedules==5 && copies==1);
    status=0; hid_ready=false;
    usb_changed(&event);
    report_quality(NULL);
    assert(cancels==3 && schedules==5 && copies==1);
    hid_ready=true; unplug_during_copy=true;
    report_quality(NULL);
    assert(copies==2 && schedules==5); /* Don't re-arm after an unplug. */
    usb_changed(&unrelated);
    assert(cancels==3 && schedules==5);
}
'''


def main():
    repo = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='trackball-usb-') as directory:
        root = Path(directory)
        (root / 'stubs.h').write_text(STUBS)
        for name in ('zephyr/device.h', 'zephyr/drivers/uart.h', 'zephyr/kernel.h',
                     'zephyr/logging/log.h', 'zmk/event_manager.h',
                     'zmk/events/usb_conn_state_changed.h', 'zmk/usb.h', 'zmk/workqueue.h'):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('#include "stubs.h"\n')
        source, binary = root / 'check.c', root / 'check'
        source.write_text(HARNESS)
        subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-I',str(root),
                        '-I',str(repo / 'config/src'),str(source),'-o',str(binary)], check=True)
        line = subprocess.check_output([str(binary)], text=True)
        value = parse_line(line)
        assert value is not None and value['squal']==110 and value['shutter']==480
        assert value['sequence']==42 and value['firmware_uptime_ms']==1234
    print('USB quality: open-console gating, suspend, unplug, error handling and wire decoding passed')


if __name__ == '__main__':
    main()
