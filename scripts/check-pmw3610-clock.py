"""Check actual register-write helpers against the PMW3610 clock-start contract."""

import argparse
from pathlib import Path
import re
import subprocess
import tempfile

from pmw3610_patch import REVISION, apply_patch, git


HARNESS = r"""
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

struct device { int unused; };
#define unlikely(x) (x)
#define LOG_ERR(...) do { if (0) printf(__VA_ARGS__); } while (0)
#define PMW3610_REG_SPI_CLK_ON_REQ 0x41
#define PMW3610_SPI_CLOCK_CMD_ENABLE 0xba
#define PMW3610_SPI_CLOCK_CMD_DISABLE 0xb5

static unsigned now_us, clock_ready_at, waits, writes, enables, disables;
static bool clock_on;
static int fail_enable, fail_disable, fail_target;

static void k_busy_wait(unsigned us) {
    now_us += us;
    waits++;
}

/* Model the data-sheet requirement independently of the production helper.
 * A target write before the clock is ready must fail, even if repeated.
 * The primitive's existing CS/inter-command delays account for 40 us.
 */
static int _reg_write(const struct device *dev, uint8_t reg, uint8_t value) {
    (void)dev;
    if (reg == PMW3610_REG_SPI_CLK_ON_REQ) {
        if (value == PMW3610_SPI_CLOCK_CMD_ENABLE) {
            enables++;
            if (fail_enable) return -EIO;
            clock_on = true;
            clock_ready_at = now_us + 300;
        } else {
            assert(value == PMW3610_SPI_CLOCK_CMD_DISABLE);
            disables++;
            if (fail_disable) return -EIO;
            clock_on = false;
        }
    } else {
        assert(clock_on);
        if (now_us < clock_ready_at) return -ETIMEDOUT;
        writes++;
        if ((int)writes == fail_target) return -EIO;
    }
    now_us += 40;
    return 0;
}

/* PRODUCTION_HELPERS */

static void reset(void) {
    now_us = clock_ready_at = waits = writes = enables = disables = 0;
    clock_on = false;
    fail_enable = fail_disable = fail_target = 0;
}

int main(void) {
    const struct device dev = {0};
    const uint8_t addr[] = {0x7f, 0x05, 0x7f};
    const uint8_t values[] = {0xff, 4, 0};
    reset();
#if BASELINE
    /* Prove the pinned upstream helpers violate the clock timing contract. */
    assert(reg_write(&dev, 0x2d, 0) == -ETIMEDOUT);
    reset();
    assert(burst_write(&dev, addr, values, 3) == -ETIMEDOUT);
    puts("Pinned upstream: both write paths fail the 300 us clock contract");
#else
    assert(reg_write(&dev, 0x2d, 0) == 0);
    assert(writes == 1 && enables == 1 && disables == 1 && !clock_on);
    assert(waits == 1);
    assert(burst_write(&dev, addr, values, 3) == 0);
    assert(writes == 4 && enables == 2 && disables == 2 && !clock_on);
    assert(waits == 2);
    for (unsigned count = 0; count <= 3; count++) {
        reset();
        assert(burst_write(&dev, addr, values, count) == 0);
        assert(writes == count && enables == 1 && disables == 1 && !clock_on);
    }
    for (int burst = 0; burst < 2; burst++) {
        reset();
        fail_enable = 1;
        int ret = burst ? burst_write(&dev, addr, values, 3) : reg_write(&dev, 0x2d, 0);
        assert(ret == -EIO && writes == 0 && waits == 0 && disables == 0);
        reset();
        fail_disable = 1;
        ret = burst ? burst_write(&dev, addr, values, 3) : reg_write(&dev, 0x2d, 0);
        assert(ret == -EIO && disables == 1);
        for (int target = 1; target <= (burst ? 3 : 1); target++) {
            reset();
            fail_target = target;
            ret = burst ? burst_write(&dev, addr, values, 3) : reg_write(&dev, 0x2d, 0);
            assert(ret == -EIO && (int)writes == target && disables == 0);
        }
    }
    puts("Patched driver: register/burst clock timing and SPI failure propagation passed");
#endif
    return 0;
}
"""


def helper(source, name):
    match = re.search(r"^static int " + re.escape(name) + r"\(.*?^\}", source, re.M | re.S)
    if not match:
        raise ValueError(f"Missing production function {name}")
    return match.group()


def check(source, baseline):
    names = ([] if baseline else ["spi_clock_enable"]) + ["reg_write", "burst_write"]
    functions = "\n\n".join(helper(source, name) for name in names)
    program = HARNESS.replace("/* PRODUCTION_HELPERS */", functions)
    with tempfile.TemporaryDirectory(prefix="pmw-clock-") as directory:
        root = Path(directory)
        cfile, binary = root / "clock.c", root / "clock"
        cfile.write_text(program)
        subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                        "-Wno-unused-function", f"-DBASELINE={int(baseline)}",
                        str(cfile), "-o", str(binary)], check=True)
        subprocess.run([str(binary)], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("driver", type=Path)
    driver = parser.parse_args().driver.resolve()
    baseline = git(driver, "show", REVISION + ":src/pmw3610.c")
    if baseline.returncode:
        raise RuntimeError(baseline.stderr)
    check(baseline.stdout, True)
    apply_patch(driver)
    check((driver / "src/pmw3610.c").read_text(), False)


if __name__ == "__main__":
    main()
