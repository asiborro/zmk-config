"""Compile and fault-test the actual patched PMW3610 initialization callback."""

import argparse
from pathlib import Path
import re
import subprocess
import tempfile

from pmw3610_patch import apply_patch


HARNESS = r"""
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

struct device { void *data; };
struct k_work { int unused; };
struct k_work_delayable { struct k_work work; };
struct gpio_callback { int unused; };
struct gpio_dt_spec { int unused; };
struct spi_dt_spec { int unused; };
#include "pixart.h"

#define CONTAINER_OF(ptr, type, field) ((type *)((char *)(ptr) - offsetof(type, field)))
#define K_MSEC(ms) (ms)
/* Type-check the real diagnostic messages too. */
#define LOG_INF(...) do { if (0) printf(__VA_ARGS__); } while (0)
#define LOG_WRN(...) LOG_INF(__VA_ARGS__)
#define LOG_ERR(...) LOG_INF(__VA_ARGS__)

/* ACTUAL_INIT_DEFINITIONS */

static int fail_stage, failures_remaining;
static int stage_calls[ASYNC_INIT_STEP_COUNT];
static int chip_select_releases, interrupt_enables;
static int scheduled, scheduled_delay, callbacks;

static int fake_stage(const struct device *dev) {
    struct pixart_data *data = dev->data;
    assert(!data->ready);
    assert(interrupt_enables == 0);
    stage_calls[data->async_init_step]++;
    if (data->async_init_step == fail_stage && failures_remaining > 0) {
        failures_remaining--;
        return -EIO;
    }
    return 0;
}

static int (*const async_init_fn[ASYNC_INIT_STEP_COUNT])(const struct device *) = {
    fake_stage, fake_stage, fake_stage, fake_stage,
};

static int spi_cs_ctrl(const struct device *dev, bool enable) {
    assert(!enable);
    assert(!((struct pixart_data *)dev->data)->ready);
    chip_select_releases++;
    return 0;
}

static void set_interrupt(const struct device *dev, bool enable) {
    assert(enable);
    assert(((struct pixart_data *)dev->data)->ready);
    interrupt_enables++;
}

static int k_work_schedule(struct k_work_delayable *work, int delay) {
    struct pixart_data *data = CONTAINER_OF(work, struct pixart_data, init_work);
    assert(!data->ready);
    assert(!scheduled);
    scheduled = 1;
    scheduled_delay = delay;
    return 1;
}

/* ACTUAL_INIT_CALLBACK */

static void check_case(int stage, int failures) {
    struct pixart_data data = {0};
    const struct device dev = {.data = &data};
    data.dev = &dev;
    fail_stage = stage;
    failures_remaining = failures;
    memset(stage_calls, 0, sizeof(stage_calls));
    chip_select_releases = interrupt_enables = callbacks = 0;
    scheduled = 1;

    while (scheduled) {
        scheduled = 0;
        pmw3610_async_init(&data.init_work.work);
        /* An unbounded retry loop must fail the test instead of hanging CI. */
        assert(++callbacks <= 3 * ASYNC_INIT_STEP_COUNT);
        if (scheduled && data.err) {
            assert(scheduled_delay == 500);
            assert(data.async_init_step == ASYNC_INIT_STEP_POWER_UP);
        } else if (scheduled) {
            assert(scheduled_delay == async_init_delay[data.async_init_step]);
        }
    }

    int attempts = failures < 3 ? failures + 1 : 3;
    int failed_attempts = failures < 3 ? failures : 3;
    bool success = failures < 3;
    assert(data.ready == success);
    assert(interrupt_enables == (success ? 1 : 0));
    assert(data.err == (success ? 0 : -EIO));
    assert(data.init_retries == (failures < 2 ? failures : 2));
    assert(chip_select_releases == failed_attempts);
    assert(stage_calls[ASYNC_INIT_STEP_POWER_UP] == attempts);
    for (int i = 0; i < ASYNC_INIT_STEP_COUNT; i++) {
        /* Every retry starts at reset, even when a late stage failed. */
        int expected = i <= stage ? attempts : (success ? 1 : 0);
        assert(stage_calls[i] == expected);
    }
}

int main(void) {
    int cases = 0;
    const int failures[] = {0, 1, 2, 3, 100};
    for (int stage = 0; stage < ASYNC_INIT_STEP_COUNT; stage++) {
        for (size_t i = 0; i < sizeof(failures) / sizeof(failures[0]); i++) {
            check_case(stage, failures[i]);
            cases++;
        }
    }
    printf("PMW3610 startup: %d fault-injection cases passed\n", cases);
    return 0;
}
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("driver", type=Path)
    args = parser.parse_args()
    driver = args.driver.resolve()
    apply_patch(driver)
    source = (driver / "src/pmw3610.c").read_text()
    # Confirm repeated configuration does not apply the patch twice.
    apply_patch(driver)
    assert (driver / "src/pmw3610.c").read_text() == source

    enum = re.search(r"enum pmw3610_init_step \{.*?\n\};", source, re.S).group()
    delays = re.search(r"static const int32_t async_init_delay\[.*?\n\};", source, re.S).group()
    constants = "\n".join(re.findall(r"^#define PMW3610_INIT_.*$", source, re.M))
    # Compile the production callback verbatim; only hardware/workqueue calls
    # are replaced with fault injection and a deterministic scheduler.
    callback = re.search(r"static void pmw3610_async_init\(struct k_work \*work\) \{.*?\n\}",
                         source, re.S).group()
    program = HARNESS.replace("/* ACTUAL_INIT_DEFINITIONS */", "\n".join([enum, delays, constants]))
    program = program.replace("/* ACTUAL_INIT_CALLBACK */", callback)
    with tempfile.TemporaryDirectory(prefix="pmw3610-startup-") as directory:
        root = Path(directory)
        for name in ("device.h", "drivers/spi.h", "drivers/gpio.h", "drivers/sensor.h"):
            path = root / "zephyr" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        cfile, binary = root / "startup.c", root / "startup"
        cfile.write_text(program)
        subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                        "-I", str(root), "-I", str(driver / "src"), str(cfile),
                        "-o", str(binary)], check=True)
        subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    main()
