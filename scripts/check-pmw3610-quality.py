"""Compile the production passive cache and fault-test its wire packets."""
import argparse
from pathlib import Path
import subprocess
import tempfile

from pmw3610_patch import apply_patch
from trackball_telemetry import decode

KERNEL = r"""
#include <assert.h>
#include <stdint.h>
#define ARG_UNUSED(x) (void)(x)
#define BIT(n) (1u << (n))
#define MIN(a,b) ((a) < (b) ? (a) : (b))
#define BUILD_ASSERT(c,m) _Static_assert(c,m)
extern int64_t test_now;
extern int lock_depth;
struct k_spinlock { int unused; };
typedef int k_spinlock_key_t;
static inline int k_spin_lock(struct k_spinlock *lock) {
    (void)lock; assert(lock_depth == 0); lock_depth++; return 0;
}
static inline void k_spin_unlock(struct k_spinlock *lock, int key) {
    (void)lock; (void)key; assert(lock_depth == 1); lock_depth--;
}
static inline int64_t k_uptime_get(void) { return test_now; }
"""
BYTEORDER = r"""
#include <stdint.h>
static inline void sys_put_le16(uint16_t v, uint8_t *p) { p[0]=v; p[1]=v>>8; }
static inline void sys_put_le32(uint32_t v, uint8_t *p) {
    p[0]=v; p[1]=v>>8; p[2]=v>>16; p[3]=v>>24;
}
"""
HARNESS = r"""
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
int64_t test_now;
int lock_depth;
#include "trackball_telemetry.c"
static uint32_t u32(const uint8_t *p) { return p[0] | p[1]<<8 | p[2]<<16 | (uint32_t)p[3]<<24; }
static uint16_t u16(const uint8_t *p) { return p[0] | p[1]<<8; }
int main(void) {
    uint8_t p[20], before[20];
    peamk_trackball_packet(p);
    assert(p[0]==1 && p[1]==0 && u16(p+8)==65535 && u32(p+10)==0);
    pmw3610_diagnostics_init(NULL, 2, 1, -EIO, false);
    peamk_trackball_packet(p);
    assert((int16_t)u16(p+6)==-EIO && p[18]==2 && p[3]==1 && !(p[1]&2));
    pmw3610_diagnostics_init(NULL, 4, 2, 0, true);
    peamk_trackball_packet(p);
    assert(p[1]==2 && p[3]==2 && p[18]==4 && u16(p+6)==0);
    uint8_t burst[7] = {0x80, 42, 1, 0, 0, 0xff, 0xff};
    test_now=1000;
    pmw3610_diagnostics_burst(NULL, burst, 0);
    peamk_trackball_packet(p);
    assert(p[1]==7 && p[2]==0 && u16(p+4)==511 && u32(p+10)==1 && u16(p+8)==0);
    memcpy(before,p,20);
    peamk_trackball_packet(p);
    assert(memcmp(before,p,20)==0); /* A Bluetooth read cannot consume or renew a sample. */
    test_now=31000;
    peamk_trackball_packet(p);
    assert(u16(p+8)==30 && u32(p+10)==1);
    test_now=70001000;
    peamk_trackball_packet(p);
    assert(u16(p+8)==65535);
    pmw3610_diagnostics_burst(NULL, NULL, -EIO); /* Do not inspect a failed SPI buffer. */
    peamk_trackball_packet(p);
    assert((int16_t)u16(p+6)==-EIO && u32(p+14)==1 && u32(p+10)==1);
    burst[0]=0; burst[4]=81; burst[5]=0; burst[6]=45;
    pmw3610_diagnostics_burst(NULL, burst, 0);
    peamk_trackball_packet(p);
    assert(p[1]==3 && p[2]==81 && u16(p+4)==45 && u16(p+6)==0);
    assert(u32(p+14)==1 && u32(p+10)==2 && u16(p+8)==0 && lock_depth==0);
    for (unsigned i=0;i<sizeof(p);i++) printf("%02x",p[i]);
    puts("");
}
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("driver", type=Path)
    args = parser.parse_args()
    driver = args.driver.resolve()
    apply_patch(driver)
    repo = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="pmw-quality-") as directory:
        root = Path(directory)
        headers = {"kernel.h": KERNEL, "sys/byteorder.h": BYTEORDER,
                   "devicetree.h": "#define DT_NUM_INST_STATUS_OKAY(x) 1\n",
                   "device.h": "#pragma once\nstruct device { int unused; };\n"}
        for name, content in headers.items():
            path = root / "zephyr" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        cfile, binary = root / "quality.c", root / "quality"
        cfile.write_text(HARNESS)
        subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                        "-I", str(root), "-I", str(driver / "src"),
                        "-I", str(repo / "config/src"), str(cfile), "-o", str(binary)], check=True)
        packet = bytes.fromhex(subprocess.check_output([str(binary)], text=True).strip())
        decoded = decode(packet)
        assert decoded["squal"] == 81 and decoded["shutter"] == 45
        assert decoded["read_errors"] == 1 and decoded["sequence"] == 2
        assert decoded["state"] == "ok" and decoded["init_retries"] == 2
    print("PMW3610 quality: startup, zero SQUAL, stale age, SPI failure and recovery passed")


if __name__ == "__main__":
    main()
