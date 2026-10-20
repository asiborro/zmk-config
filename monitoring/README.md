# darkblue and roseblue measurements

The Linux host samples both Bluetooth battery services every **five minutes**.
It keeps every attempt, including missing readings, in
`~/.local/share/peamk-battery/history.sqlite3`. A local HTTP exporter serves the
latest sample without extra Bluetooth polling. Prometheus on `ci4.test` scrapes
it every 15 seconds through an SSH tunnel and retains 30 days. The local database
has no automatic expiry and can be exported to CSV.

The collector runs while this Linux user's services run. It starts again at
login and restarts after failures. Switching the keyboard to the Mac, shutting
down/suspending Linux, or disconnecting Bluetooth creates gaps; it does not
record zero or invent readings. The original Bluetooth percentage can be cached inside the right firmware.
Telemetry firmware additionally exposes the left connection and sample age;
these are used to reject stale estimates.
Polling also means these tests include the small monitoring workload.

## Telemetry firmware

Flash `darkblue-left` and `darkblue-right` for the main split keyboard. The right relays the
left's telemetry through its existing split connection. The left never needs
a direct connection to the computer. Pairings and keyboard/mouse descriptors
are preserved; the additional GATT service is appended after existing services.
The host may need one ordinary Bluetooth reconnect to discover it.

Every five seconds while powered and awake (including idle), each half takes five
ADC samples on ZMK's low-priority battery queue and reports their median plus
the last raw value. Sampling does not reset the inactivity timer; deep sleep
still occurs after 15 minutes. The right polls left telemetry every five seconds.
The host still collects every five minutes, including during USB power.

**Power is inferred from supply voltage, not detected from a separate USB pin.**
The [Lemon v2 schematic](https://github.com/rianadon/Cosmos-Keyboard-PCBs/blob/main/lemon-wireless-uc/lemon-wireless-schematic-v02.pdf)
uses an MS88SF21 module whose [internal USB configuration](https://store.minewsemi.com/wp-content/uploads/2024/03/MS88SF2-nRF52840_Datasheet_K_EN-1.pdf)
connects VBUS to VDDH (page 7, R2). The original telemetry firmware read
VBUSDETECT and incorrectly identified a charged battery as USB power.
The reader now ignores that flag in old packets and uses fresh supply voltage:
at most 4250 mV suggests battery, at least 4400 mV suggests USB, and the interval
between them is uncertain. A weak or current-limited USB supply can overlap the
battery range, so this remains an inference.

**USB power does not prove the battery is charging or full**: neither the
battery switch nor the charger CHG pin is exposed to this firmware. On Lemon
v2, VDDH is the charger's system output, not a separate battery terminal
measurement. The supply-voltage graph therefore rises on USB, while the
charge-estimate graph has a gap. Waybar shows `USB` with an explanation in its
tooltip. Power annotations recorded using the old VBUS detector are retained
in history, but excluded from the dashboard's power timeline.
The normal OS Bluetooth battery widget still reads ZMK's original percentage.

Readings older than 180 seconds or from a disconnected left half cannot produce
a charge estimate. With protocol v2, after a power transition estimates wait two minutes;
discharge comparisons wait ten minutes. Detected power and RGB/brightness take
priority over manual annotations. A change in power, LEDs, brightness, firmware
boot, curve, or collection continuity starts a new comparison segment. Capacity
remains a manual label. All raw history is retained, including zeros and USB.

Protocol v1 remains readable without reflashing: its incorrect VBUS flag and
transition age are ignored, and the flag is stored separately for diagnosis.
Its unplug settling time is unknown, so its immediate voltage estimate is
provisional. Comparisons still wait ten minutes of observed stable conditions.
Flashing both halves upgrades to v2 and speeds sampling/relay from 60 to five
seconds. With Waybar's five-second refresh, a normal update takes up to roughly
ten seconds on the right and fifteen on the left, plus Bluetooth delays. The
old firmware can take about two minutes on the left even with faster Waybar
refresh. The two-minute estimate settling window is separate from USB display.

### Calculation and calibration

The calculation uses the median voltage, floating-point interpolation, bounds
of 0–100%, and rejection of stale/USB/unsettled samples. The default curve uses
ZMK's 3450 mV and 4200 mV endpoints. **It is not calibrated to these cells**; a
smoother graph is not evidence of more accurate remaining mAh. There is no
current sensor and no charging-completion signal. We retain the original
percentage for comparison instead of rewriting historical readings.

A measured nonlinear curve can be installed without another firmware flash at
`~/.config/peamk-battery/curve.json`, with `left` and/or `right` lists of
`[millivolts, percent]` pairs in strictly increasing order. Endpoints must be
0% and 100%. Omitted halves use the default. The file below reproduces the
uncalibrated endpoints; replace it only with measurements appropriate to your
cells and load, rather than another battery's chart:

```json
{"left": [[3450, 0], [4200, 100]], "right": [[3450, 0], [4200, 100]]}
```

The collector reloads the curve each sample. Waybar uses the same file and
calculation. A curve change gets a distinct method label to avoid joining
incompatible estimates into a drain-rate comparison. Full-charge-to-recharge
runtime with consistent usage and LEDs remains the practical sizing test.

### Read-only Bluetooth protocol v2 (v1 compatible)

Service UUID: `50ea0000-2f54-4e31-a2b7-6cb20f70a901`. Characteristic `50ea0001`
(same UUID suffix) reports the local half; on the central, `50ea0002` reports
its single left peripheral. Both require encryption. Each value is 16 bytes,
with little-endian integers and a default-MTU-compatible payload:

| Offset | Type | Meaning |
| --- | --- | --- |
| 0 | u8 | Version, 2; relayed v1 packets retain their original version |
| 1 | u8 | Flags: bit 0 ADC valid, 1 USB inferred, 2 power inference available, 3 RGB on, 4 RGB known, 5 connected |
| 2 | u8 | Configured brightness 0–100, or 255 unknown |
| 3 | u8 | Metadata relay age in seconds, saturating at 255 |
| 4 | u16 | Median supply millivolts |
| 6 | u16 | ADC sample age, including relay time; 65535 unknown/saturated |
| 8 | u32 | Successful sample counter, resets at boot |
| 12 | u16 | Seconds since voltage-inferred power-source change, saturating at 65535; ignored for v1 |
| 14 | u16 | Last unfiltered supply millivolts |

The central clears cached peer telemetry on disconnection/reconnection and
never reuses a pre-reconnect response. Failed/stalled reads age out. The
host validates version, length, flags, voltage range and timestamps before
using any values. No telemetry operation writes settings or changes RGB.

## Start and maintain a test

With telemetry firmware, USB and RGB labels update automatically. With older
firmware, `darkblue-battery mark` records labels only and cannot detect charging.
It remains useful for capacity and experiment notes with either firmware.
Never infer USB absence merely because this computer cannot see it: a charger
or Mac can supply power too.

```sh
# After physically unplugging both halves, with LEDs on:
darkblue-battery mark --power battery --leds on --note 'Normal use, LEDs on'

# After cutting LED power with Esc/Media + K in the latest keymap:
darkblue-battery mark --leds off --note 'Normal use, LEDs off'

# Before charging just the right half:
darkblue-battery mark --half right --power usb

# After unplugging just the right half:
darkblue-battery mark --half right --power battery

# Optional: known brightness or a different battery capacity:
darkblue-battery mark --half both --brightness 15 --capacity-mah 750

darkblue-battery status
darkblue-battery report --hours 24
darkblue-battery export > battery-history.csv
```

Esc/Media + K sends RGB-off followed by LED-power-off to both connected halves;
Esc/Media + G restores LED power and solid cyan at 10%. Q, 0% brightness and the
one-minute idle timer leave the LED supply enabled, so those are different
power conditions. Telemetry reports RGB state and brightness, not a measurement
of the LED supply. Add a note after using K to distinguish a deliberate power
cut from ordinary dark/idle LEDs. The trackball supply is separate.

Unknown conditions stay unknown. With old firmware, saved brightness is not
available, so the collector does not assume the 15% firmware default.
Each annotation starts a new comparison period with a ten-minute settling
window. USB, unknown power/LED state, missing readings and clipped 0%/100%
readings remain in raw history but are excluded from discharge comparisons.
The report also splits periods at gaps over 12.5 minutes, condition changes,
and upward jumps greater than three percentage points. It does not average a
drop across a charging cycle or a disconnected interval.

For the first comparison, charge both halves properly, unplug, and keep the
same brightness/effect and normal usage for a day. Then repeat with LEDs off
from a comparable charge. Mark long idle periods with a new `--note` if they
should be analyzed separately. A full charge-to-recharge run is more useful
for sizing than a short percentage slope: Lemon estimates battery percentage
from supply voltage, and USB can report 100% even with an empty battery.

If a measured 750 mAh battery lasts T hours under the desired usage, 1500,
2000, and 3000 mAh give approximately 2T, 2.67T, and 4T under comparable load.
Choose against the worse-performing half and leave some reserve. Actual cell
capacity, fit and usage matter; percentage readings cannot measure mAh/current.
The [Lemon documentation](https://ryanis.cool/cosmos/docs/pcbs/lemon-wireless/#power)
specifies 100 mA default charging, so larger batteries also charge much longer.

## Metrics and dashboard

After deployment, the dashboard is at
[darkblue](http://ci4.test:3000/d/peamk-battery/darkblue) and
[roseblue](http://ci4.test:3000/d/roseblue/roseblue).

- `peamk_battery_reported_ratio{half="left|right"}`: raw estimated charge, 0–1.
- `peamk_battery_estimated_ratio{half,method}`: usable charge estimate, absent on USB.
- `peamk_supply_voltage_volts{half}`: median supply voltage; not cell voltage on USB.
- `peamk_supply_raw_voltage_volts{half}`: last unfiltered ADC sample.
- `peamk_voltage_sample_age_seconds{half}`: actual ADC sample age including relay.
- `peamk_reporting_state_info{half,state}`: USB/battery/settling/stale/unavailable.
- `peamk_condition_source_info{half,source}`: voltage inference or manual power annotation.
- `peamk_leds_source_info{half,source}`: firmware or manual RGB annotation.
- `peamk_battery_discharge_ratio{half,leds}`: usable battery-only subset.
- `peamk_battery_read_success{half}` and `peamk_bluetooth_connected`: visibility.
- `peamk_sample_age_seconds`: collection age, **not** the left firmware's age.
- `peamk_test_condition_info{half,leds,power}`: effective context (firmware takes priority).
- `peamk_led_brightness_ratio{half}`: detected or annotated brightness, NaN if unknown.
- `peamk_battery_capacity_coulombs{half}`: annotated capacity (2700 initially;
  divide by 3600 for Ah).

Multiply ratio metrics by 100 to display percentages. Avoid `rate()` on the
battery gauge and avoid fitting discharge slopes across USB or missing data.
The local report fits a linear regression through all original five-minute
samples within each uninterrupted comparison segment, rather than using just
two endpoints or counting repeated Prometheus scrapes as new battery data.
With telemetry it also reports the voltage slope. Neither slope is a current
measurement or a validated prediction of remaining runtime.

## Installation and operations

Requires Python 3, PyGObject/Gio, BlueZ and systemd user services. Installation
does not flash firmware. `install-local.sh` installs the collector, CLI,
and the user service files, and starts both collectors by default.
Pass `--with-tunnel` to also start sending data to the configured server.
An already enabled legacy tunnel is migrated automatically.
The tunnel uses existing SSH access to `ci4.test` and
binds only to loopback at both ends; no new LAN-facing port is opened.

When `~/.config/waybar/scripts` exists, the installer also updates the Waybar
reader and installs the matching dark background, 10 px corners and 210 px
minimum width. It imports only the battery stylesheet, backing up the main
stylesheet first. Merge [waybar-battery.jsonc](waybar-battery.jsonc) into the
existing config for a five-second refresh and add `custom/peamk-battery` to
`modules-right`. Reload Waybar with `pkill -USR2 -x waybar`. Disconnected and
missing readings retain `L -- R --`, so the widget does not disappear or shrink.
Waybar reads only the two telemetry characteristics when available; the
five-minute collector also saves the original battery-service percentages.

```sh
monitoring/install-local.sh
# Once this is the intended Prometheus destination:
monitoring/install-local.sh --with-tunnel
python3 monitoring/deploy-prometheus.py ci4.test
systemctl --user status darkblue-battery roseblue-trackball keyboard-telemetry-tunnel
journalctl --user -u darkblue-battery -u roseblue-trackball --since today
curl http://127.0.0.1:19108/metrics
```

The deployment adds the two jobs in `prometheus-job.yml`, validates the complete
config with the server's `promtool`, backs up the previous config, and signals
Prometheus to reload. If Docker still sees a replaced configuration file, it
restarts only Prometheus to refresh that mount. It installs both dashboards in the existing provisioned
folder. Existing Pi jobs and dashboards are preserved. Re-run this deployment
after rebuilding `/opt/monitoring` from its separate infrastructure repository,
which otherwise replaces these additions. Local samples remain available when
the tunnel/server is unavailable; they are not automatically backfilled into
Prometheus.

Stop with `systemctl --user disable --now keyboard-telemetry-tunnel darkblue-battery roseblue-trackball`.
This preserves the history for later analysis.

## Trackball quality over Bluetooth

The right half appends a read-only, encrypted characteristic
`50ea0003-2f54-4e31-a2b7-6cb20f70a901` to the existing telemetry service. Existing
HID, BAS and battery telemetry handles and the v2 battery packets remain unchanged.
The left firmware does not need this addition. Both normal and debug right
images include quality telemetry.

The Inorichi motion burst already contains SQUAL and shutter. Passive observers
cache those bytes plus startup status and read errors under a short spin lock.
There is no extra SPI read, sensor wake-up, force-awake setting or activity event.
A Bluetooth read copies a 20-byte snapshot, which fits a default ATT read response.

Protocol v1 (all multibyte values little-endian):

| Offset | Type | Meaning |
| --- | --- | --- |
| 0 | u8 | Version: 1 |
| 1 | u8 | Flags: bit 0 sample exists, bit 1 initialized, bit 2 last burst motion bit |
| 2 | u8 | Raw SQUAL register, 0–255 |
| 3 | u8 | Startup retries used, 0–2 |
| 4 | u16 | Nine-bit shutter value, 0–511 clock cycles |
| 6 | i16 | Last init/read errno; zero after success |
| 8 | u16 | Last successful burst age in seconds, saturates at 65535 |
| 10 | u32 | Successful bursts since boot |
| 14 | u32 | Failed motion-burst reads since boot |
| 18 | u8 | Initialization stage: 0–3; 4 means ready |
| 19 | u8 | Reserved, zero |

The collector polls only this cached characteristic every **10 seconds** and
saves every attempt in SQLite `trackball_samples`. Battery sampling stays at
five minutes. Neither idle nor charging stops collection. Deep sleep, another
host profile and link loss appear disconnected; old firmware appears unsupported.
The ordinary Waybar path does not add trackball Bluetooth reads.

`peamk_trackball_squal` and `peamk_trackball_shutter_cycles` are available only
when the sensor is ready, the last operation succeeded, and the sample is no
more than **30 seconds** old. Missing data is NaN, never an invented zero.
`peamk_trackball_sample_age_seconds` includes time elapsed in the collector cache.
Stale means there has been no recent sensor burst: this is expected while idle
but can also indicate stalled acquisition. It is not itself evidence of bad optics.

Other metrics include `peamk_trackball_ready`, `peamk_trackball_last_error`,
`peamk_trackball_init_retries`, `peamk_trackball_init_stage`,
`peamk_trackball_quality_available`, `peamk_trackball_collection_success`,
`peamk_trackball_state_info`, `peamk_trackball_bursts_total` and
`peamk_trackball_read_errors_total`. Counters reset at firmware boot. The
collector reads every ten seconds, so graphs are sampled diagnostics and do
not record every instantaneous motion burst.

SQUAL is **not a percentage or a distance measurement**. Compare the same ball
and similar movement; there is no universal good/bad threshold. Shutter counts
are not milliseconds. See the [PMW3610 datasheet](https://trackballs.eu/media/Nakabayashi/Digio2/PMW3610DM-SUDU.pdf).
The battery dashboard adds raw SQUAL, shutter, sample age, sensor state and
read-error panels with the same styling as existing panels. No fresh SQUAL
appears until the updated right firmware is flashed and the ball is moved.

## USB trackball measurements

`darkblue-right-debug` and `roseblue-right` enable `CONFIG_PEAMK_TRACKBALL_USB_TELEMETRY`. A low-priority
work item copies the same 20-byte packet and emits one structured log line
every two seconds while USB is configured, not suspended, and CDC ACM DTR is
asserted. Disconnect/suspend cancels the work; a configured USB connection
without an open console schedules checks but emits no snapshots. The reporter
does not read SPI, generate input events, change endpoints or enable a shell.
Bluetooth telemetry remains available in the same firmware.

The line's payload is `PEAMK_TB1 <uptime_ms> <40 hexadecimal characters>`.
`uptime_ms` is an unsigned 32-bit millisecond counter, wrapping after about
49 days. The hexadecimal packet uses the existing Bluetooth protocol above;
the ordinary logger prefix and ANSI color escapes are not part of the payload.
No keycodes or pointer coordinates appear in this structured record.

`scripts/trackball_usb.py --serial YOUR_SERIAL --seconds 30` selects the exact
device through `/dev/serial/by-id/`, asserts DTR at 115200 baud, and prints
decoded JSON with `usb_serial`, `transport`, `received_at` and firmware uptime.
It requires access to the serial port. An optional `--port` supports an explicitly
verified device mapping, such as a restricted container passed only that port.
The reader never writes console commands or uses the 1200-baud bootloader touch.

The first four seconds and first frame are discarded. Subsequent frames must
advance firmware uptime at approximately their observed arrival cadence;
duplicate records, rapid bursts of buffered logs and reset discontinuities are
discarded. Sample age is the firmware's age at emission; it does not measure
USB/logging latency. Old firmware produces an explicit timeout rather than a
fabricated zero. The reader drops unrelated console lines and bounds partial
line buffering. The one-shot reader prints JSON. The persistent roseblue collector described
below stores those samples in its own history and Prometheus job.

## Separate device collection

| Device | Selection | Database | Cached metrics |
| --- | --- | --- | --- |
| darkblue | Bluetooth `D5:B0:5B:EC:16:5D` | `~/.local/share/peamk-battery/history.sqlite3` | `127.0.0.1:19108` |
| roseblue | USB serial `047B8C32401DC31D` | `~/.local/share/roseblue/history.sqlite3` | `127.0.0.1:19109` |

Both services run indefinitely, including idle, charging and unplugged periods.
Roseblue reads structured USB frames continuously and stores a snapshot every
10 seconds. It retries the exact serial after disconnect, never opens the
bootloader port, and drops cached quality after eight seconds without frames.
A database is bound to its device serial and rejects a different device.
`no_console` means the hardware is attached but its USB diagnostics port is
absent; flash the named roseblue-right image. `waiting` means the console is
open but no advancing structured frames have arrived yet. Move the ball for
fresh optical readings; `stale` is expected when no recent sensor burst exists.

The roseblue dashboard contains only USB/sensor diagnostics and comparison
plots against darkblue. It does not invent a left half or battery measurements
for the USB-only collection. The firmware driver, CPI, polling and motion
transform initially match; USB logging and the device names differ.

The firmware's existing `PEAMK_*` protocol identifiers, metric prefixes,
darkblue's historical database path and original Grafana UID are retained for
compatibility. Display names, service names and current scrape jobs use the new
names. Darkblue's dashboard includes the old `peamk-battery` job when querying
history. No existing samples are deleted or relabeled. The old CLI is an alias
for `darkblue-battery`. Waybar pins darkblue's Bluetooth address and preserves
the existing widget style.

Serial access is required for the user running `roseblue-trackball.service`.
The single SSH tunnel forwards both loopback ports; other Pi monitoring jobs
are preserved. Adjust the hardware IDs in the service files when installing
this repository for different physical devices.
