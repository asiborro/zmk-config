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
           connected=True, valid=True, leds=True, version=2):
    flags = (int(valid) | int(usb) << 1 | 4 | int(leds) << 3 | 16 | int(connected) << 5)
    return struct.pack("<BBBBHHIHH", version, flags, 15, relay_age, mv, age, sequence, power_age, mv + 5)


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

    def test_no_power_claim_without_a_fresh_valid_voltage(self):
        data = reader.decode_telemetry(packet(usb=True, mv=4950, valid=False, age=65535))
        self.assertEqual(data["state"], "unavailable")
        self.assertIsNone(data["usb"])
        self.assertIsNone(data["voltage_mv"])

    def test_lemon_v2_vbus_flag_on_battery_is_not_usb(self):
        data = reader.decode_telemetry(packet(version=1, usb=True, mv=3760, power_age=0))
        self.assertEqual(data["state"], "battery")
        self.assertFalse(data["usb"])
        self.assertTrue(data["reported_usb"])
        self.assertAlmostEqual(data["estimate"], 41.33)
        self.assertIsNone(data["power_age"])

    def test_power_inference_requires_voltage_outside_the_ambiguous_band(self):
        for version in (1, 2):
            with self.subTest(version=version):
                self.assertEqual(reader.decode_telemetry(packet(version=version, mv=4950))["state"], "usb")
                self.assertEqual(reader.decode_telemetry(packet(version=version, mv=4400))["state"], "usb")
                self.assertEqual(reader.decode_telemetry(packet(version=version, mv=4250))["state"], "battery")
                for mv in (4251, 4300, 4399):
                    data = reader.decode_telemetry(packet(version=version, mv=mv))
                    self.assertEqual(data["state"], "uncertain")
                    self.assertIsNone(data["usb"])
                    self.assertIsNone(data["estimate"])

    def test_unplugging_waits_for_settling_and_fresh_voltage(self):
        self.assertIsNone(reader.decode_telemetry(packet(power_age=20))["estimate"])
        self.assertIsNone(reader.decode_telemetry(packet(age=200))["estimate"])
        self.assertIsNone(reader.decode_telemetry(packet(mv=4950))["estimate"])
        self.assertEqual(reader.decode_telemetry(packet())["estimate"], 60)

    def test_malformed_or_unknown_packets_are_rejected(self):
        for value in (b"", packet()[:-1], bytes([3]) + packet()[1:], packet(mv=0)):
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

    def test_read_only_service_maps_local_to_right_and_peer_to_left(self):
        device = "/org/bluez/hci0/dev_test"
        service = device + "/service0090"
        local, peer = service + "/char0092", service + "/char0094"
        objects = {device: {reader.DEVICE: {"Alias": "PeaMK", "Connected": True}},
                   service: {reader.SERVICE: {"UUID": reader.TELEMETRY_SERVICE}},
                   local: {reader.CHARACTERISTIC: {"Service": service, "UUID": reader.TELEMETRY_LOCAL}},
                   peer: {reader.CHARACTERISTIC: {"Service": service, "UUID": reader.TELEMETRY_PEER}}}
        values = {local: packet(usb=True, mv=4950), peer: packet(mv=3900)}
        levels = reader.battery_levels(objects, lambda path, _: values[path])
        self.assertEqual(reader.waybar_output(levels)["text"], "PeaMK L 60% R USB")
        values[peer] = b"bad"
        levels = reader.battery_levels(objects, lambda path, _: values[path])
        self.assertEqual(reader.waybar_output(levels)["text"], "PeaMK L -- R USB")

    def test_curve_change_gets_a_new_estimate_method(self):
        first = reader.decode_telemetry(packet(), [[3450, 0], [4200, 100]])
        second = reader.decode_telemetry(packet(), [[3400, 0], [4200, 100]])
        self.assertNotEqual(first["method"], second["method"])


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
        self.assertEqual(rows[1]["power_source"], "voltage-inference")
        self.assertEqual(rows[1]["leds_source"], "firmware")
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

    def test_old_vbus_chatter_does_not_restart_battery_analysis(self):
        self.collect(1000, version=1, usb=True, mv=3760, power_age=5)
        self.collect(1300, version=1, usb=False, mv=3755, power_age=10)
        rows = self.collect(1600, version=1, usb=True, mv=3750, power_age=0)
        self.assertTrue(rows[0]["eligible"])
        self.assertEqual(rows[0]["condition_since"], 1000)
        self.assertEqual(rows[0]["power"], "battery")
        self.assertEqual(rows[0]["reported_usb"], 1)
        self.assertIsNone(rows[0]["power_age_seconds"])

    def test_firmware_upgrade_starts_a_new_comparison_segment(self):
        self.collect(1000, version=1)
        self.collect(1300, version=1)
        self.assertTrue(self.collect(1600, version=1)[0]["eligible"])
        rows = self.collect(1900, version=2)
        self.assertFalse(rows[0]["eligible"])
        self.assertEqual(rows[0]["condition_since"], 1900)

    def test_disconnect_does_not_restore_old_manual_usb_labels(self):
        monitor.mark(self.db, monitor.HALVES, {"power": "usb", "leds": "on"}, now=0)
        self.collect(1000)
        for now in (1300, 1600):
            rows = monitor.collect(self.db, lambda: dict(connected=False, left=None, right=None), now=now)
            self.assertEqual(rows[0]["reporting_state"], "disconnected")
            self.assertEqual(rows[0]["power"], "unknown")
            self.assertEqual(rows[0]["leds"], "unknown")
            self.assertIn('peamk_usb_power{half="left"} NaN', monitor.exposition(rows, now))
        self.assertFalse(self.collect(1900)[0]["eligible"])

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

    def test_stale_exporter_does_not_keep_claiming_usb_power(self):
        rows = self.collect(1000, usb=True, mv=4950)
        metrics = monitor.exposition(rows, 1700)
        self.assertIn('peamk_usb_power{half="right"} NaN', metrics)
        self.assertNotIn('peamk_reporting_state_info{', metrics)

    def test_manual_experiment_note_starts_a_new_automatic_segment(self):
        self.collect(1000)
        self.collect(1300)
        self.assertTrue(self.collect(1600)[0]["eligible"])
        monitor.mark(self.db, monitor.HALVES, {}, "new experiment", now=1800)
        self.assertFalse(self.collect(1900)[0]["eligible"])

    def test_report_uses_all_samples_instead_of_only_endpoints(self):
        monitor.mark(self.db, monitor.HALVES, {"power": "battery", "leds": "on"}, now=0)
        for i in range(13):
            value = 80 - i - (3 if i == 12 else 0)
            monitor.collect(self.db, lambda: dict(connected=True, left=value, right=value), now=1000+i*300)
        output = monitor.report(self.db, 24, now=4600)
        self.assertIn("13.19 percentage points/hour; regression over 13 samples", output)


if __name__ == "__main__":
    unittest.main()
