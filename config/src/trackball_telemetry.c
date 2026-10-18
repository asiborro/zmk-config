/* SPDX-License-Identifier: MIT */
/* Passive observers: no SPI, extra timers, logging or input activity. */
#include <zephyr/devicetree.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/byteorder.h>
#include "pmw3610_diagnostics.h"
#include "trackball_telemetry.h"

BUILD_ASSERT(DT_NUM_INST_STATUS_OKAY(pixart_pmw3610) == 1,
             "Trackball diagnostics support one PMW3610");

static struct k_spinlock quality_lock;
static struct {
    bool valid;
    bool ready;
    bool motion;
    uint8_t squal;
    uint8_t retries;
    uint8_t stage;
    uint16_t shutter;
    int16_t error;
    uint32_t sequence;
    uint32_t read_errors;
    int64_t sampled_at;
} quality;

void pmw3610_diagnostics_init(const struct device *dev, uint8_t stage,
                            uint8_t retries, int error, bool ready) {
    ARG_UNUSED(dev);
    k_spinlock_key_t key = k_spin_lock(&quality_lock);
    quality.ready = ready;
    quality.retries = retries;
    quality.stage = stage;
    quality.error = error;
    k_spin_unlock(&quality_lock, key);
}

void pmw3610_diagnostics_burst(const struct device *dev, const uint8_t *burst, int error) {
    ARG_UNUSED(dev);
    k_spinlock_key_t key = k_spin_lock(&quality_lock);
    quality.error = error;
    if (error) {
        quality.read_errors++;
    } else {
        quality.squal = burst[4];
        quality.shutter = ((uint16_t)(burst[5] & 1) << 8) | burst[6];
        quality.motion = (burst[0] & BIT(7)) != 0;
        quality.sequence++;
        quality.sampled_at = k_uptime_get();
        quality.valid = true;
    }
    k_spin_unlock(&quality_lock, key);
}

void peamk_trackball_packet(uint8_t packet[PEAMK_TRACKBALL_PACKET_SIZE]) {
    k_spinlock_key_t key = k_spin_lock(&quality_lock);
    packet[0] = 1;
    packet[1] = (quality.valid ? BIT(0) : 0) | (quality.ready ? BIT(1) : 0) |
                (quality.motion ? BIT(2) : 0);
    packet[2] = quality.squal;
    packet[3] = quality.retries;
    sys_put_le16(quality.shutter, packet + 4);
    sys_put_le16((uint16_t)quality.error, packet + 6);
    sys_put_le16(quality.valid ? MIN((k_uptime_get() - quality.sampled_at) / 1000,
                                   UINT16_MAX) : UINT16_MAX, packet + 8);
    sys_put_le32(quality.sequence, packet + 10);
    sys_put_le32(quality.read_errors, packet + 14);
    packet[18] = quality.stage;
    packet[19] = 0;
    k_spin_unlock(&quality_lock, key);
}
