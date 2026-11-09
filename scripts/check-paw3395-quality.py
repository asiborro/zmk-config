"""Fault-test PAW3395 burst acquisition and the production quality cache."""
import argparse
from pathlib import Path
import runpy
import subprocess
import tempfile

from paw3395_patch import apply_patch
from trackball_telemetry import decode

# Reuse the same kernel/byte-order stubs as the legacy sensor's cache test.
STUBS = runpy.run_path(str(Path(__file__).with_name('check-pmw3610-quality.py')))
HARNESS = r'''
#include <errno.h>
#include <stdio.h>
#include <string.h>
int64_t test_now;
int lock_depth;
#include "trackball_telemetry.c"
static uint32_t u32(const uint8_t *p) { return p[0] | p[1]<<8 | p[2]<<16 | (uint32_t)p[3]<<24; }
static uint16_t u16(const uint8_t *p) { return p[0] | p[1]<<8; }
struct pixart_data { bool ready; int32_t dx, dy; };
struct pixart_config { int spi, evt_type, x_input_code, y_input_code; };
static uint8_t test_burst[12];
static int spi_error, reads, inputs;
#define IS_ENABLED(x) 1
#define PAW3395_BURST_SIZE 6
#define PAW3395_DX_POS 2
#define PAW3395_DY_POS 4
#define unlikely(x) (x)
#define LOG_WRN(...)
#define LOG_DBG(...)
#define CLAMP(x,l,h) ((x)<(l)?(l):((x)>(h)?(h):(x)))
#define K_NO_WAIT 0
static uint16_t sys_get_le16(const uint8_t *p) { return u16(p); }
static int paw3395_lib_motion_burst_read(const int *spi, uint8_t *buf, size_t size) {
    (void)spi; assert(size==12); reads++;
    if (!spi_error) memcpy(buf,test_burst,size);
    return spi_error;
}
static void input_report(const struct device *dev, int type, int code, int value, bool sync, int wait) {
    (void)dev; (void)type; (void)code; (void)value; (void)sync; (void)wait; inputs++;
}
/* The real driver report function is inserted below. */
REPORT_FUNCTION
int main(void) {
    uint8_t p[24], before[24];
    struct pixart_data data = {0};
    struct pixart_config config = {0};
    struct device dev = {.data=&data, .config=&config};
    keyboard_trackball_packet(p);
    assert(p[0]==2 && p[19]==1 && p[1]==0 && u16(p+8)==65535);
    assert(paw3395_report_data(&dev)==-EBUSY && reads==0);
    paw3395_diagnostics_init(&dev,1,-EIO,false);
    keyboard_trackball_packet(p);
    assert((int16_t)u16(p+6)==-EIO && p[18]==1 && p[3]==0);
    data.ready=true;
    paw3395_diagnostics_init(&dev,3,0,true);
    /* Zero movement and zero SQUAL must still update the cache. */
    uint8_t sample[12]={0x80,0xa5,0,0,0,0,0,73,200,9,0xff,0xff};
    memcpy(test_burst,sample,12); test_now=1000;
    assert(paw3395_report_data(&dev)==0 && inputs==0 && reads==1);
    keyboard_trackball_packet(p);
    assert(p[1]==7 && p[2]==0 && p[18]==3 && u16(p+4)==4095 && u32(p+10)==1);
    assert(p[20]==73 && p[21]==200 && p[22]==9 && p[23]==0xa5);
    memcpy(before,p,24); keyboard_trackball_packet(p);
    assert(memcmp(p,before,24)==0 && reads==1);
    test_now=32000; keyboard_trackball_packet(p);
    assert(u16(p+8)==31 && u32(p+10)==1);
    test_now=70001000; keyboard_trackball_packet(p);
    assert(u16(p+8)==65535);
    spi_error=-EIO;
    assert(paw3395_report_data(&dev)==-EIO);
    keyboard_trackball_packet(p);
    assert((int16_t)u16(p+6)==-EIO && u32(p+14)==1 && u32(p+10)==1);
    /* Recovery preserves normal 1:1 deltas; shutter is upper byte first. */
    spi_error=0; test_burst[0]=0; test_burst[2]=7; test_burst[4]=9;
    test_burst[6]=81; test_burst[10]=0x02; test_burst[11]=0x34;
    assert(paw3395_report_data(&dev)==0 && inputs==2);
    keyboard_trackball_packet(p);
    assert(p[1]==3 && p[2]==81 && u16(p+4)==0x234 && u16(p+6)==0);
    assert(u32(p+14)==1 && u32(p+10)==2 && u16(p+8)==0 && lock_depth==0);
    for (unsigned i=0;i<sizeof(p);i++) printf("%02x",p[i]);
    puts("");
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('driver', type=Path)
    args = parser.parse_args()
    driver = args.driver.resolve()
    apply_patch(driver)
    apply_patch(driver)  # Reconfiguration must be idempotent.
    source = (driver / 'src/paw3395.c').read_text()
    report = source.split('static int paw3395_report_data(', 1)[1].split('static void paw3395_gpio_callback', 1)[0]
    report = 'static int paw3395_report_data(' + report
    repo = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='paw-quality-') as directory:
        root = Path(directory)
        headers = {'kernel.h': STUBS['KERNEL'], 'sys/byteorder.h': STUBS['BYTEORDER'],
                   'devicetree.h': '#define DT_NUM_INST_STATUS_OKAY(x) 1\n',
                   'device.h': '#pragma once\nstruct device { void *data; const void *config; };\n'}
        for name, content in headers.items():
            path = root / 'zephyr' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        cfile, binary = root / 'quality.c', root / 'quality'
        cfile.write_text('#include <stdint.h>\n' + HARNESS.replace('REPORT_FUNCTION', report))
        subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror','-DCONFIG_PAW3395=1',
                        '-I',str(root),'-I',str(driver / 'src'),'-I',str(repo / 'config/src'),
                        str(cfile),'-o',str(binary)], check=True)
        packet = bytes.fromhex(subprocess.check_output([str(binary)], text=True).strip())
        value = decode(packet)
        assert value['sensor']=='paw3395' and value['shutter']==0x234 and value['squal']==81
        assert value['raw_sum']==73 and value['raw_max']==200 and value['raw_min']==9
        assert value['read_errors']==1 and value['sequence']==2 and value['state']=='ok'
    print('PAW3395: full burst, zero movement/SQUAL, 12-bit shutter, age, SPI failure and recovery passed')


if __name__ == '__main__':
    main()
