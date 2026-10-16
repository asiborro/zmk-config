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
record zero or invent readings. The left percentage may still be cached inside
the right firmware, which does not expose its age or the left connection state.
Polling also means these tests include the small monitoring workload.

## Start and maintain a test

The `peamk-battery` command **records labels only**; it cannot switch keyboard
LEDs or detect charging. Record a change whenever plugging/unplugging either
half or changing LEDs, brightness, battery, or usage pattern. Never assume
USB is absent just because this computer cannot see it: a charger or the Mac
can supply USB power too.

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

Unknown conditions stay unknown. The firmware's default brightness is 15%, but
saved keyboard settings can override it, so the collector does not assume 15%.
Each annotation starts a new comparison period with a ten-minute settling
window. USB, unknown power/LED state, missing readings and saturated 100%
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
- `peamk_battery_discharge_ratio{half,leds}`: annotated battery-only subset.
- `peamk_battery_read_success{half}` and `peamk_bluetooth_connected`: visibility.
- `peamk_sample_age_seconds`: collection age, **not** the left firmware's age.
- `peamk_test_condition_info{half,leds,power}`: manual context.
- `peamk_led_brightness_ratio{half}`: annotated brightness, NaN if unknown.
- `peamk_battery_capacity_coulombs{half}`: annotated capacity (2700 initially;
  divide by 3600 for Ah).

Multiply ratio metrics by 100 to display percentages. Avoid `rate()` on the
battery gauge and avoid fitting discharge slopes across USB or missing data.
The local report uses original five-minute samples instead of counting the
repeated one-minute Prometheus scrapes as additional measurements.

## Installation and operations

Requires Python 3, PyGObject/Gio, BlueZ and systemd user services. No firmware
change or reflash is involved. `install-local.sh` installs the collector, CLI,
and both user service files, and starts only the local collector by default.
Pass `--with-tunnel` to also start sending data to the configured server.
The tunnel uses existing SSH access to `ci4.test` and
binds only to loopback at both ends; no new LAN-facing port is opened.

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
