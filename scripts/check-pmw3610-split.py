"""Compile the actual mode selector for peripheral, central and unibody builds."""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile

from pmw3610_patch import apply_patch


HARNESS = r"""
#include <assert.h>
#include <stddef.h>
#include <stdint.h>
#define IS_ENABLED(option) (option)
#define ARG_UNUSED(value) (void)(value)
enum pixart_input_mode { MOVE, SCROLL, SNIPE };
struct pixart_config {
    const int32_t *scroll_layers;
    size_t scroll_layers_len;
    const int32_t *snipe_layers;
    size_t snipe_layers_len;
};
struct device { const struct pixart_config *config; };
#if !CONFIG_ZMK_SPLIT || CONFIG_ZMK_SPLIT_ROLE_CENTRAL
static uint8_t layer;
static uint8_t zmk_keymap_highest_layer_active(void) { return layer; }
#endif
/* PRODUCTION_SELECTOR */
int main(void) {
    const int32_t scrolling[] = {8}, sniping[] = {7};
    const struct pixart_config config = {scrolling, 1, sniping, 1};
    const struct device device = {&config};
#if CONFIG_ZMK_SPLIT && !CONFIG_ZMK_SPLIT_ROLE_CENTRAL
    /* No keymap implementation exists on this side: a reference cannot link. */
    assert(get_input_mode_for_current_layer(&device) == MOVE);
    assert(get_input_mode_for_current_layer(NULL) == MOVE);
#else
    layer = 0; assert(get_input_mode_for_current_layer(&device) == MOVE);
    layer = 8; assert(get_input_mode_for_current_layer(&device) == SCROLL);
    layer = 7; assert(get_input_mode_for_current_layer(&device) == SNIPE);
    layer = 5; assert(get_input_mode_for_current_layer(&device) == MOVE);
#endif
}
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("driver", type=Path)
    args = parser.parse_args()
    driver = args.driver.resolve()
    apply_patch(driver)
    source = (driver / "src/pmw3610.c").read_text()
    selector = re.search(r"^static enum pixart_input_mode get_input_mode_for_current_layer\(.*?^\}",
                         source, re.M | re.S)
    if selector is None:
        raise ValueError("Missing production input-mode selector")
    with tempfile.TemporaryDirectory(prefix="pmw-split-") as directory:
        root = Path(directory)
        cfile = root / "mode.c"
        cfile.write_text(HARNESS.replace("/* PRODUCTION_SELECTOR */", selector.group()))
        for name, split, central in (("peripheral", 1, 0), ("central", 1, 1), ("unibody", 0, 0)):
            binary = root / name
            subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                            f"-DCONFIG_ZMK_SPLIT={split}", f"-DCONFIG_ZMK_SPLIT_ROLE_CENTRAL={central}",
                            str(cfile), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)
    print("PMW3610 split: raw peripheral motion and central/unibody layer modes passed")


if __name__ == "__main__":
    main()
