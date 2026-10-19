# Cosmos Lemon Wireless firmware

ZMK configuration for a split Cosmos keyboard with a PMW3610 trackball on
the **right (central)** half. The existing Colemak-DH keymap, mouse transform,
scroll layer, and boot keys are retained.

## Firmware versions

- ZMK **v0.3.0**, the latest published stable release checked on 2026-10-16.
- Cosmos-recommended [Inorichi PMW3610 driver](https://github.com/inorichi/zmk-pmw3610-driver)
  pinned to `1d9c2c68ca76012e1b1e5f6ef02fa5eadc4ca399`, with a local
  [startup retry patch](patches/pmw3610-startup-retries.patch), passive quality
  observers, and a [SPI clock timing correction](patches/pmw3610-write-clock-delay.patch).
- Zephyr 3.5 build image pinned by digest to match this ZMK release.
- Local Lemon board and boot-key module; provenance and license are in
  [`config/UPSTREAM`](config/UPSTREAM) and [`config/LICENSE-ZMK`](config/LICENSE-ZMK).

The trackball is configured directly in the right shield. The older VIK
add-on is unnecessary and is no longer imported.

## Build and flash

GitHub Actions reads [`build.yaml`](build.yaml) on pushes to `main` and pull
requests. Each artifact includes `zmk.uf2`, the compiled devicetree and
Kconfig settings, and a manifest recording exact dependency revisions.

| Artifact prefix | Purpose |
| --- | --- |
| `left-` | Left peripheral firmware |
| `right-` | Right central firmware with trackball |
| `reset-` | Optional recovery image; clears saved settings and pairings |
| `right-debug-` | Right firmware with USB sensor diagnostics |

This keyboard uses **v0.2 (v2) Lemon Wireless controllers**. All automatic
builds select `cosmos_lemon_wireless`, the target for v0.3 and earlier. The board
name is appended to artifact names.

The upstream board files also include `cosmos_lemon_wireless_v4` for v0.4
controllers, which use the opposite LED-power control polarity. That optional
target is available under **Run workflow**, but is not used by this keyboard.

Flash the matching left and right UF2 files to the corresponding `LEMONBOOT`
drives after double-tapping each controller's reset button. Use normal firmware
for everyday battery operation. The reset image is for recovery, not a required
step in this update; flashing it erases existing pairings.

For a local build with the Zephyr 3.5 toolchain, run from this repository:

```sh
west init -l config
west update --fetch-opt=--filter=tree:0
west zephyr-export
west build -s zmk/app -d build/right -b cosmos_lemon_wireless -- \
  -DSHIELD=peamk_r2c_right \
  -DZMK_CONFIG="$PWD/config" -DZMK_EXTRA_MODULES="$PWD/config"
```

Use `peamk_r2c_left` and a separate build directory for the left side. The
`ZMK_EXTRA_MODULES` argument loads the local Lemon board/boot-key module.
For right-half builds, this module also applies the startup retry, passive
quality-observer and SPI clock timing patches at
configure time. The patch helper accepts only the pinned driver revision and
is safe to run repeatedly, including with caches from earlier patch stacks.
The frozen manifest records all applied patch hashes.

Before each register-write group, the driver now waits **300 microseconds**
after enabling the sensor's SPI clock. This is required by the
[PMW3610 datasheet, page 13](https://trackballs.eu/media/Nakabayashi/Digio2/PMW3610DM-SUDU.pdf).
The pinned upstream driver omitted that wait in both its single-register and
multi-register write paths. This corrects a protocol timing violation; whether
it resolves the observed battery startup self-test failure still needs hardware
validation. It does not change the LED configuration or bypass the self-test.

The driver makes one normal initialization attempt. If a sensor startup step
fails, it releases chip select and retries the complete reset/self-test/configure
sequence up to **two more times**, waiting **500 ms** before each retry.
After three failed attempts it stops; debug
firmware logs the failed step, error code, retries, and final failure. The retries
address transient initialization failures; they do not establish the cause of
the intermittent trackball failure or recover failures after initialization.

CI checks the actual write helpers against the clock-start requirement, including
demonstrating the failure with the unpatched driver. It also injects failures at
every initialization stage to check recovery, the retry limit, and that motion
interrupts remain disabled until startup succeeds. To run
the same checks locally after `west update`:

```sh
python3 scripts/check-pmw3610-startup.py zmk-pmw3610-driver
python3 scripts/check-pmw3610-clock.py zmk-pmw3610-driver
python3 scripts/check-pmw3610-quality.py zmk-pmw3610-driver
```

## Left and right battery levels

### Trackball optical quality

The right firmware exposes PMW3610 **SQUAL**, shutter, sample age, startup status
and SPI read-error counts over Bluetooth. Values are cached from normal motion
bursts; reading diagnostics does not perform sensor I/O or reset the idle timer.

The monitor stores these diagnostics every **10 seconds**, independently of
five-minute battery history. Prometheus scrapes cached metrics every 15 seconds.
The [battery dashboard](http://ci4.test:3000/d/peamk-battery/peamk-battery) includes
trackball panels below the battery panels. Move the ball to get new samples;
values older than 30 seconds are unavailable rather than reported as zero.

Only the **right half** needs flashing for this addition. Both normal and debug
right images include it. Existing HID/battery handles and battery protocol v2
are preserved. If diagnostics remain `unsupported` after flashing, reconnect
to refresh Bluetooth service discovery before considering re-pairing.

SQUAL is a raw register, not a percentage or distance measurement. There is no
universal good/bad threshold; compare the same ball under similar movement.
Shutter is a clock-cycle count. This feature measures behavior and does not
claim to fix the intermittent startup/idle fault. See
[protocol and metric details](monitoring/README.md#trackball-quality-over-bluetooth).

### Trackball quality over USB

The **right-debug** image supports both Bluetooth quality telemetry and USB
quality output. It emits a cached diagnostic packet every two seconds while
USB is configured, awake and the serial console is open. Reporting stops on
USB disconnect or suspend and performs no extra sensor reads. The normal
right image keeps Bluetooth quality telemetry without the USB debug console.

Select the physical keyboard by the serial number in `/dev/serial/by-id/`:

```sh
python3 scripts/trackball_usb.py --serial YOUR_SERIAL --seconds 30
```

Run with permission to open that serial device. The reader emits JSON containing
only trackball diagnostics and the selected USB serial number; other debug
output is discarded. It discards startup buffers and requires advancing device
timestamps before reporting values. Move the ball to produce fresh samples.
USB sample age is measured when the firmware emits its snapshot; it excludes
any subsequent transport delay.

Only the chosen keyboard's right half needs flashing. The same image works on
either keyboard when both use the Lemon Wireless v2 right-side configuration.
A filename such as `cosmos-wired-right-usb-debug.uf2` distinguishes the download;
it does not restrict the firmware to one physical keyboard. This reader is for
USB measurements; the existing Bluetooth collector and Grafana series continue
to describe the Bluetooth keyboard.

### LED brightness and battery testing

This keyboard has **23 LEDs on the left and 18 on the right**. The right shield's
board overlay overrides the shared 23-LED default for both Lemon revisions.

Both halves default to **10% RGB brightness**. LEDs turn off after **60 seconds
of inactivity** and return to their previous on/off state on activity. The
existing 15-minute deep-sleep timeout is separate from this LED idle timer.

RGB and LED-power settings survive firmware updates, so flashing alone will not
replace a previously saved off state or brightness. Hold **Esc/Media** and tap
**G** to explicitly enable LED power and turn on **solid cyan at 10% on both
halves**. This recovery preset works even when the two halves disagree about
their saved state. Only the right (central) half needs the updated keymap; it
sends the existing power and RGB commands to the left. Keep both halves connected
and use the keyboard for at least a minute to allow the deferred settings save.
Esc/Media + **B** increases brightness, **J** decreases it, **Q** toggles LEDs, and
**W** cycles effects. The breathing effect in ZMK v0.3 varies brightness separately;
use the solid preset for a repeatable 10% battery comparison.

For an LEDs-off battery test, hold **Esc/Media** and tap **K**. This stops RGB
updates, then cuts the separate LED supply on both connected halves. Use **G**
to restore LED power and the 10% preset. Q and the one-minute idle timer only
turn off the RGB output; 0% brightness also leaves LED power enabled. After K,
Q alone cannot restore the power supply. The K shortcut requires only the
updated right-half keymap; the existing left firmware supports both commands.
It does not switch off the keyboard, Bluetooth or the separate trackball supply.

The preset provides a way to recover and test the software state; it does not
prove the cause of a single stuck green LED. If the right remains dark after
the preset while telemetry reports on at 10%, the LED signal/power path still
needs investigation. The LED power control is independent of the sensor's
3.3 V supply in this board configuration.

Use the normal left/right images for battery comparisons; USB debug logging
changes power consumption. Verify 10% for both halves in telemetry, then measure
each half from a full charge to needing a recharge under normal use. Existing
voltage percentages and short discharge segments cannot establish week-long
runtime. A 3000 mAh battery offers roughly four times the runtime of a 750 mAh
battery at the same load; it is a candidate, not a verified one-week size.

### Monitoring

For five-minute history, Prometheus/Grafana tracking, and LED-on/off comparisons,
see [battery monitoring](monitoring/README.md). The logger runs on Linux;
the detailed voltage, RGB and sample-age readings require telemetry firmware.

Only the right half pairs with the computer. It reports its own battery and
relays the left half's battery over that same Bluetooth connection. The right
configuration enables ZMK's peripheral battery fetching and proxy service.
The left half continues to connect only to the right.

Flash **both halves** for the latest five-second telemetry and corrected power
inference. The reader also supports the previous one-minute telemetry firmware.
Percentages are estimates from voltage. USB power is inferred from the supply
voltage; charge level and completion cannot be measured while on USB. Most
desktop battery widgets still show only one of the original battery services.

[`scripts/waybar-battery.py`](scripts/waybar-battery.py) reads both services from
the existing BlueZ connection and emits Waybar JSON with explicit `L` and `R`
labels. It identifies the left service by ZMK's `Peripheral 0` descriptor, not
by the order of Bluetooth services. Missing readings display `--`.
It requires Python 3 and PyGObject/Gio and never pairs or connects either half.

Copy it to `~/.config/waybar/scripts/waybar-battery.py`, add
`custom/peamk-battery` to Waybar's `modules-right`, and add this module:

```json
"custom/peamk-battery": {
    "exec": "python3 ~/.config/waybar/scripts/waybar-battery.py",
    "return-type": "json",
    "interval": 5,
    "format": "{}",
    "tooltip": true,
    "escape": true
}
```

`monitoring/install-local.sh` also installs the matching battery stylesheet
when the Waybar scripts directory exists. It preserves a minimum width even
when the keyboard is disconnected. See the monitoring guide for installation.

After flashing, reconnect the right half if the additional service is not
discovered. Keep both halves awake long enough for a battery update. The
reader's tests run with `python3 -m unittest discover -s scripts -p 'test_*.py'`.

## Trackball failure with the physical switch on

The owner reported the keyboard and trackball working after flashing the
ZMK/Inorichi update on 2026-10-16. The original failure's exact cause was not
isolated. The following diagnostics are retained in case it returns.

The physical switch controls the battery
charger's SYSOFF input; it is not a firmware Bluetooth-output selector.
On the published Lemon schematics, the VIK 3.3 V supply comes from its own
regulator. ZMK's `ext_power` controls the LED supply, so changing RGB power
settings does not directly switch the standard VIK trackball supply.

To distinguish a sensor startup failure from a failure while running:

1. Flash the `right-debug-<matching-board>` image on the right half.
2. Connect it over USB and open its serial console (for example, `tio
   /dev/ttyACM0` on Linux; choose the device assigned to this keyboard).
3. Reset with the physical switch off, then reset with it on. Keep the same
   USB cable and output selection. Save both logs, including startup messages.
4. Also test switching on after the trackball has started working, without
   resetting. Note whether tracking stops immediately or fails only after boot.

Look for `PMW3610 initialized`, `Incorrect product id`, `Failed self-test`,
or `PMW3610 initialization failed`. Report the PCB revision and whether keys
continue working during the failure. These observations determine whether to
investigate sensor initialization, supply/wiring, or motion-event delivery.

If the trackball works over USB but fails specifically when **Bluetooth is
selected as the output**, refresh the host's cached HID descriptor by removing
the keyboard, clearing that Bluetooth profile on the keyboard, and pairing
again. That is a separate test from turning the physical switch on.

References: [ZMK releases](https://github.com/zmkfirmware/zmk/releases),
[USB logging](https://zmk.dev/docs/development/usb-logging),
[Bluetooth HID descriptor refresh](https://zmk.dev/docs/features/bluetooth#refreshing-the-hid-descriptor),
and [Lemon v0.3 schematic](https://github.com/rianadon/Cosmos-Keyboard-PCBs/blob/main/lemon-wireless-uc/lemon-wireless-schematic-v03.pdf).
