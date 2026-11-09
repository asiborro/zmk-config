/* SPDX-License-Identifier: MIT */
/* Passive observers: no SPI, extra timers, logging or input activity. */
#include <zephyr/devicetree.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/byteorder.h>
#ifdef CONFIG_PAW3395
#include "paw3395_diagnostics.h"
BUILD_ASSERT(DT_NUM_INST_STATUS_OKAY(pixart_paw3395) == 1,
             "Trackball diagnostics support one PAW3395");
#else
#include "pmw3610_diagnostics.h"
BUILD_ASSERT(DT_NUM_INST_STATUS_OKAY(pixart_pmw3610) == 1,
             "Trackball diagnostics support one PMW3610");
#endif
#include "trackball_telemetry.h"

static struct k_spinlock quality_lock;
static struct {
    bool valid;
    bool ready;
    bool motion;
    uint8_t squal;
    uint8_t retries;
    uint8_t stage;
    uint16_t shutter;
#ifdef CONFIG_PAW3395
    uint8_t raw_sum, raw_max, raw_min, observation;
#endif
    int16_t error;
    uint32_t sequence;
    uint32_t read_errors;
    int64_t sampled_at;
} quality;

#ifdef CONFIG_PAW3395
void paw3395_diagnostics_init(const struct device *dev, uint8_t stage,
                             int error, bool ready) {
    const uint8_t retries = 0; /* This driver makes one startup attempt. */
#else
void pmw3610_diagnostics_init(const struct device *dev, uint8_t stage,
                            uint8_t retries, int error, bool ready) {
#endif
    ARG_UNUSED(dev);
    k_spinlock_key_t key = k_spin_lock(&quality_lock);
    quality.ready = ready;
    quality.retries = retries;
    quality.stage = stage;
    quality.error = error;
    k_spin_unlock(&quality_lock, key);
}

#ifdef CONFIG_PAW3395
void paw3395_diagnostics_burst(const struct device *dev, const uint8_t *burst, int error) {
#else
void pmw3610_diagnostics_burst(const struct device *dev, const uint8_t *burst, int error) {
#endif
    ARG_UNUSED(dev);
    k_spinlock_key_t key = k_spin_lock(&quality_lock);
    quality.error = error;
    if (error) {
        quality.read_errors++;
    } else {
#ifdef CONFIG_PAW3395
        quality.squal = burst[6];
        /* Full motion burst puts the upper shutter byte first (12 bits). */
        quality.shutter = ((uint16_t)(burst[10] & 0x0f) << 8) | burst[11];
        quality.raw_sum = burst[7];
        quality.raw_max = burst[8];
        quality.raw_min = burst[9];
        quality.observation = burst[1];
#else
        quality.squal = burst[4];
        quality.shutter = ((uint16_t)(burst[5] & 1) << 8) | burst[6];
#endif
        quality.motion = (burst[0] & BIT(7)) != 0;
        quality.sequence++;
        quality.sampled_at = k_uptime_get();
        quality.valid = true;
    }
    k_spin_unlock(&quality_lock, key);
}

void keyboard_trackball_packet(uint8_t packet[KEYBOARD_TRACKBALL_PACKET_SIZE]) {
    k_spinlock_key_t key = k_spin_lock(&quality_lock);
#ifdef CONFIG_PAW3395
    packet[0] = 2;
#else
    packet[0] = 1;
#endif
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
#ifdef CONFIG_PAW3395
    packet[19] = 1; /* Sensor ID: PAW3395. */
    packet[20] = quality.raw_sum;
    packet[21] = quality.raw_max;
    packet[22] = quality.raw_min;
    packet[23] = quality.observation;
#else
    packet[19] = 0;
#endif
    k_spin_unlock(&quality_lock, key);
}
