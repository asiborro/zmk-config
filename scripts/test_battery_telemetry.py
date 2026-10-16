import importlib.util
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


reader = module("telemetry_reader", "waybar-battery.py")
monitor = module("telemetry_monitor", "battery-monitor.py")


def packet(usb=False, mv=3900, age=10, relay_age=0, sequence=1, power_age=2000,
           connected=True, valid=True, leds=True):
    flags = (int(valid) | int(usb) << 1 | 4 | int(leds) << 3 | 16 | int(connected) << 5)
    return struct.pack("<BBBBHHIHH", 1, flags, 15, relay_age, mv, age, sequence, power_age, mv + 5)


class TelemetryTest(unittest.TestCase):
    def test_usb_never_becomes_full_battery(self):
        data = reader.decode_telemetry(packet(usb=True, mv=4980))
        self.assertEqual(data["state"], "usb")
        self.assertEqual(data["voltage_mv"], 4980)
        self.assertIsNone(data["estimate"])
        levels = dict(connected=True, left=100, right=100, telemetry=dict(left=data, right=data))
        output = reader.waybar_output(levels)
        self.assertEqual(output["text"], "PeaMK L USB R USB")
        self.assertIn("completion unknown", output["tooltip"])

    def test_stale_or_disconnected_peer_cannot_supply_charge_or_usb_state(self):
        for raw in (packet(usb=True, connected=False), packet(usb=True, relay_age=200)):
            data = reader.decode_telemetry(raw)
            self.assertIsNone(data["estimate"])
            self.assertIsNone(data["usb"])
            self.assertIsNone(data["voltage_mv"])

    def test_fresh_usb_metadata_survives_an_adc_failure(self):
        data = reader.decode_telemetry(packet(usb=True, valid=False, age=65535))
        self.assertEqual(data["state"], "usb")
        self.assertIsNone(data["voltage_mv"])

    def test_unplugging_waits_for_settling_and_fresh_voltage(self):
        self.assertIsNone(reader.decode_telemetry(packet(power_age=20))["estimate"])
        self.assertIsNone(reader.decode_telemetry(packet(age=200))["estimate"])
        self.assertIsNone(reader.decode_telemetry(packet(mv=4950))["estimate"])
        self.assertEqual(reader.decode_telemetry(packet())["estimate"], 60)

    def test_malformed_or_unknown_packets_are_rejected(self):
        for value in (b"", packet()[:-1], bytes([2]) + packet()[1:], packet(mv=0)):
            with self.assertRaises(ValueError):
                reader.decode_telemetry(value)

    def test_calibrated_curve_is_bounded_monotonic_and_interpolated(self):
        curve = [[3300, 0], [3700, 30], [3850, 70], [4200, 100]]
        self.assertEqual(reader.voltage_percent(3775, curve), 50)
        self.assertEqual(reader.voltage_percent(3000, curve), 0)
        self.assertEqual(reader.voltage_percent(4300, curve), 100)
        results = [reader.voltage_percent(mv, curve) for mv in range(3000, 4300)]
        self.assertEqual(results, sorted(results))
        with self.assertRaises(ValueError):
            reader.voltage_percent(3700, [[4200, 100], [3300, 0]])


class TelemetryStorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "samples.sqlite3"
        self.db = monitor.database(self.path)
        self.addCleanup(self.db.close)

    def collect(self, now, **kwargs):
        telemetry = reader.decode_telemetry(packet(sequence=int(now), **kwargs))
        return monitor.collect(self.db, lambda: dict(connected=True, left=100, right=100,
            telemetry={"left": telemetry, "right": telemetry}), now=now)

    def test_detected_power_overrides_manual_annotations_and_usb_excludes_estimate(self):
        monitor.mark(self.db, monitor.HALVES, {"power": "battery", "leds": "off"}, now=0)
        rows = self.collect(1000, usb=True, mv=4950)
        self.assertEqual(rows[1]["power"], "usb")
        self.assertEqual(rows[1]["leds"], "on")
        self.assertEqual(rows[1]["eligible"], 0)
        metrics = monitor.exposition(rows, 1000)
        self.assertIn('peamk_supply_voltage_volts{half="right"} 4.95', metrics)
        self.assertNotIn('peamk_battery_estimated_ratio{', metrics)
        self.assertNotIn('peamk_battery_discharge_ratio{', metrics)

    def test_auto_conditions_require_settling_and_break_on_usb_or_led_changes(self):
        self.collect(1000)
        self.collect(1300)
        rows = self.collect(1600)
        self.assertTrue(rows[0]["eligible"])
        self.assertEqual(rows[0]["condition_since"], 1000)
        rows = self.collect(1900, leds=False)
        self.assertFalse(rows[0]["eligible"])
        rows = self.collect(2200, usb=True, mv=4950)
        self.assertFalse(rows[0]["eligible"])

    def test_power_transition_between_polls_restarts_analysis(self):
        self.collect(1000)
        self.collect(1300)
        self.collect(1600)
        rows = self.collect(1900, power_age=200)
        self.assertEqual(rows[0]["condition_since"], 1700)
        self.assertFalse(rows[0]["eligible"])

    def test_migration_keeps_existing_rows_and_annotations(self):
        old_path = Path(self.tmp.name) / "old.sqlite3"
        old = sqlite3.connect(old_path)
        old.execute("CREATE TABLE samples (timestamp REAL, half TEXT, connected INTEGER, level INTEGER, error TEXT, leds TEXT, power TEXT, brightness INTEGER, capacity_mah INTEGER, condition_since REAL, eligible INTEGER, PRIMARY KEY(timestamp,half))")
        old.execute("INSERT INTO samples VALUES(1,'right',1,37,NULL,'on','battery',15,750,0,1)")
        old.commit()
        old.close()
        migrated = monitor.database(old_path)
        self.addCleanup(migrated.close)
        row = dict(migrated.execute("SELECT * FROM samples").fetchone())
        self.assertEqual(row["level"], 37)
        self.assertEqual(row["telemetry"], 0)
        self.assertIsNone(row["voltage_mv"])

    def test_zero_is_retained_but_not_used_as_a_discharge_slope(self):
        monitor.mark(self.db, monitor.HALVES, {"power": "battery", "leds": "on"}, now=0)
        rows = monitor.collect(self.db, lambda: dict(connected=True, left=0, right=0), now=1000)
        self.assertEqual(rows[0]["level"], 0)
        self.assertEqual(rows[0]["eligible"], 0)


if __name__ == "__main__":
    unittest.main()
