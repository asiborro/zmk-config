# Battery measurements

The Linux host samples both Bluetooth battery services every **five minutes**.
It keeps every attempt, including missing readings, in
`~/.local/share/peamk-battery/history.sqlite3`. A local HTTP exporter serves the
latest sample without extra Bluetooth polling. Prometheus on `ci4.test` scrapes
it every minute through an SSH tunnel and retains 30 days. The local database
has no automatic expiry and can be exported to CSV.

The collector runs while this Linux user's services run. It starts again at
login and restarts after failures. Switching the keyboard to the Mac, shutting
down/suspending Linux, or disconnecting Bluetooth creates gaps; it does not
record zero or invent readings. The original Bluetooth percentage can be cached inside the right firmware.
Telemetry firmware additionally exposes the left connection and sample age;
these are used to reject stale estimates.
Polling also means these tests include the small monitoring workload.

## Telemetry firmware

Flash both `cosmos-lemon-wireless-*-telemetry.uf2` images. The right relays the
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
firmware, `peamk-battery mark` records labels only and cannot detect charging.
It remains useful for capacity and experiment notes with either firmware.
Never infer USB absence merely because this computer cannot see it: a charger
or Mac can supply power too.

```sh
# After physically unplugging both halves, with LEDs on:
peamk-battery mark --power battery --leds on --note 'Normal use, LEDs on'

# After physically toggling LEDs off (hold Esc/Media, tap Q):
peamk-battery mark --leds off --note 'Normal use, LEDs off'

# Before charging just the right half:
peamk-battery mark --half right --power usb

# After unplugging just the right half:
peamk-battery mark --half right --power battery

# Optional: known brightness or a different battery capacity:
peamk-battery mark --half both --brightness 15 --capacity-mah 750

peamk-battery status
peamk-battery report --hours 24
peamk-battery export > battery-history.csv
```

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
<http://ci4.test:3000/d/peamk-battery/peamk-battery>.

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
two endpoints or counting repeated one-minute Prometheus scrapes as new data.
With telemetry it also reports the voltage slope. Neither slope is a current
measurement or a validated prediction of remaining runtime.

## Installation and operations

Requires Python 3, PyGObject/Gio, BlueZ and systemd user services. Installation
does not flash firmware. `install-local.sh` installs the collector, CLI,
and both user service files, and starts only the local collector by default.
Pass `--with-tunnel` to also start sending data to the configured server.
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
systemctl --user status peamk-battery peamk-battery-tunnel
journalctl --user -u peamk-battery --since today
curl http://127.0.0.1:19108/metrics
```

The deployment adds only the job in `prometheus-job.yml`, validates the complete
config with the server's `promtool`, backs up the previous config, and signals
Prometheus to reload. If Docker still sees a replaced configuration file, it
restarts only Prometheus to refresh that mount. It installs the dashboard in the existing provisioned
folder. Existing Pi jobs and dashboards are preserved. Re-run this deployment
after rebuilding `/opt/monitoring` from its separate infrastructure repository,
which otherwise replaces these additions. Local samples remain available when
the tunnel/server is unavailable; they are not automatically backfilled into
Prometheus.

Stop with `systemctl --user disable --now peamk-battery-tunnel peamk-battery`.
This preserves the history for later analysis.
