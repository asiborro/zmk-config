import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("monitor", Path(__file__).with_name("battery-monitor.py"))
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


class BatteryMonitorTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "history.sqlite3"
        self.db = monitor.database(self.path)
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(self.db.close)

    def collect(self, now=1000, left=65, right=35, connected=True):
        return monitor.collect(self.db, lambda: dict(connected=connected, left=left, right=right), now=now)

    def annotate(self, now=0, **changes):
        monitor.mark(self.db, monitor.HALVES, {"leds": "on", "power": "battery", **changes}, now=now)

    def test_unannotated_values_are_saved_but_not_used_for_discharge(self):
        rows = self.collect()
        self.assertEqual([row["level"] for row in rows], [65, 35])
        self.assertEqual([row["eligible"] for row in rows], [0, 0])

    def test_usb_and_full_scale_readings_are_excluded(self):
        self.annotate()
        monitor.mark(self.db, ("right",), {"power": "usb"}, now=0)
        rows = self.collect(right=100)
        self.assertEqual([row["eligible"] for row in rows], [1, 0])
        metrics = monitor.exposition(rows, 1000)
        self.assertIn('peamk_battery_reported_ratio{half="right"} 1.0', metrics)
        self.assertNotIn('peamk_battery_discharge_ratio{half="right"', metrics)
        monitor.mark(self.db, ("right",), {"power": "battery"}, now=0)
        self.assertEqual(self.collect(now=1300, right=100)[1]["eligible"], 0)

    def test_mark_starts_a_settling_period_and_survives_reopening(self):
        self.annotate(now=900, leds="off")
        self.assertEqual(self.collect()[0]["eligible"], 0)
        reopened = monitor.database(self.path)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.execute("SELECT brightness FROM conditions LIMIT 1").fetchone()[0], 0)
        self.assertEqual(self.collect(now=1500)[0]["eligible"], 1)

    def test_disconnected_and_invalid_readings_never_turn_into_zero(self):
        rows = self.collect(connected=False)
        self.assertEqual([row["level"] for row in rows], [None, None])
        rows = self.collect(now=1300, left=255, right=0)
        self.assertIsNone(rows[0]["level"])
        self.assertEqual(rows[1]["level"], 0)

    def test_bus_failure_is_recorded_as_unknown_and_collection_continues(self):
        def fail():
            raise RuntimeError("bus unavailable")
        rows = monitor.collect(self.db, fail, now=1000)
        self.assertIsNone(rows[0]["connected"])
        self.assertIn("bus unavailable", rows[0]["error"])
        self.assertEqual(self.collect(now=1300)[0]["level"], 65)

    def test_stale_collections_remove_battery_metrics(self):
        self.annotate()
        metrics = monitor.exposition(self.collect(), 1700)
        self.assertNotIn('peamk_battery_reported_ratio{', metrics)
        self.assertNotIn('peamk_battery_discharge_ratio{', metrics)
        self.assertIn("peamk_collection_success 0", metrics)
        self.assertIn("peamk_bluetooth_connected NaN", metrics)
        self.assertIn("peamk_collection_success 0", monitor.exposition([], 1700))

    def test_conditions_do_not_change_retroactively(self):
        self.annotate()
        self.collect()
        self.annotate(now=1200, leds="off")
        self.collect(now=1900)
        rows = list(self.db.execute("SELECT leds FROM samples WHERE half='left' ORDER BY timestamp"))
        self.assertEqual([row[0] for row in rows], ["on", "off"])

    def test_led_toggle_does_not_invent_a_brightness(self):
        self.annotate(leds="off")
        self.annotate(leds="on")
        self.assertIsNone(self.db.execute("SELECT brightness FROM conditions LIMIT 1").fetchone()[0])

    def test_gaps_changes_and_charging_jumps_split_analysis(self):
        self.annotate()
        rows = self.collect(now=1000)
        rows += self.collect(now=1300, left=64, right=34)
        rows += self.collect(now=1600, left=90, right=90)
        rows += self.collect(now=2600, left=89, right=89)
        self.annotate(now=2700, leds="off")
        rows += self.collect(now=3400, left=88, right=88)
        groups = monitor.segments(rows)
        self.assertEqual([len(group) for group in groups], [2, 1, 1, 1, 2, 1, 1, 1])

    def test_report_describes_observed_drain_without_inventing_runtime(self):
        self.annotate()
        for index in range(13):
            self.collect(now=1000 + index * 300, left=80 - index, right=60 - index)
        output = monitor.report(self.db, 24, now=4600)
        self.assertIn("13 collection attempts", output)
        self.assertIn("80% -> 68% in 1.00 h (12.00 percentage points/hour; regression over 13 samples)", output)
        self.assertIn("short slopes cannot establish full runtime", output)


if __name__ == "__main__":
    unittest.main()
