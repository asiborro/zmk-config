import importlib.util
from pathlib import Path
import sqlite3
import unittest

import trackball_telemetry as track


def packet(**changes):
    values = dict(version=1, flags=7, squal=90, retries=1, shutter=511, error=0,
                  age=1, sequence=40, errors=2, stage=4, reserved=0)
    values.update(changes)
    return track.PACKET.pack(*values.values())


class QualityTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        track.initialize(self.db)
        self.addCleanup(self.db.close)

    def collect(self, now=100, **changes):
        return track.collect(self.db, lambda: track.decode(packet(**changes)), now)

    def test_wire_zero_quality_and_shutter_are_real_not_missing(self):
        row = self.collect(squal=0, shutter=0)
        self.assertEqual(track.latest(self.db)["squal"], 0)
        metrics = track.exposition(row, 100)
        self.assertIn('peamk_trackball_squal{half="right"} 0', metrics)
        self.assertIn('peamk_trackball_quality_available{half="right"} 1', metrics)

    def test_startup_no_sample_and_old_sample_are_distinct(self):
        for changes, expected in ((dict(flags=0), "not_ready"), (dict(flags=2), "no_sample"),
                                  (dict(age=31), "stale"), (dict(age=65535), "stale")):
            with self.subTest(expected=expected):
                data = track.decode(packet(**changes))
                self.assertEqual(data["state"], expected)
                self.assertIsNone(data["squal"])

    def test_read_error_overrides_a_recent_old_success(self):
        data = track.decode(packet(error=-5, age=0))
        self.assertEqual(data["state"], "error")
        self.assertIsNone(data["squal"])
        self.assertEqual(data["last_error"], -5)
        self.assertEqual(data["read_errors"], 2)

    def test_invalid_packets_are_rejected(self):
        for data in (b"", packet()[:-1], packet(version=2), packet(flags=8),
                     packet(reserved=1), packet(shutter=512), packet(error=1)):
            with self.assertRaises(ValueError):
                track.decode(data)

    def test_export_includes_elapsed_cache_time_without_renewing_samples(self):
        row = self.collect(age=25)
        text = track.exposition(row, 106)
        self.assertIn('peamk_trackball_sample_age_seconds{half="right"} 31', text)
        self.assertIn('peamk_trackball_squal{half="right"} NaN', text)
        self.assertIn('state="stale"', text)

    def test_stopped_collection_cannot_claim_live_sensor_status(self):
        text = track.exposition(self.collect(), 121)
        self.assertIn('peamk_trackball_ready{half="right"} NaN', text)
        self.assertIn('peamk_trackball_quality_available{half="right"} 0', text)
        self.assertNotIn('peamk_trackball_bursts_total{', text)

    def test_reboot_resets_counters_without_losing_prior_history(self):
        self.collect(sequence=1000)
        row = self.collect(now=110, sequence=1, errors=0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM trackball_samples").fetchone()[0], 2)
        self.assertIn('# TYPE peamk_trackball_bursts_total counter', track.exposition(row, 110))
        self.assertIn('peamk_trackball_bursts_total{half="right"} 1', track.exposition(row, 110))

    def test_collection_failure_is_stored_and_following_reads_recover(self):
        def fail():
            raise RuntimeError("BlueZ unavailable")
        row = track.collect(self.db, fail, now=100)
        self.assertEqual(row["state"], "unavailable")
        self.assertIsNone(row["connected"])
        self.assertEqual(self.collect(now=110)["state"], "ok")

    def test_old_firmware_and_disconnect_do_not_become_zero_quality(self):
        for value in (dict(connected=True, state="unsupported"), dict(connected=False, state="disconnected")):
            row = track.collect(self.db, lambda: value, now=100 + len(value["state"]))
            self.assertIsNone(row["squal"])
            self.assertIn('peamk_trackball_squal{half="right"} NaN', track.exposition(row, row["timestamp"]))

    def test_device_service_and_uuid_are_all_checked(self):
        spec = importlib.util.spec_from_file_location("reader", Path(__file__).with_name("waybar-battery.py"))
        reader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(reader)
        dev, svc, char = "/device", "/device/service", "/device/service/char"
        objects = {dev: {reader.DEVICE: {"Alias": "PeaMK", "Connected": True}},
                   svc: {reader.SERVICE: {"UUID": reader.TELEMETRY_SERVICE}},
                   char: {reader.CHARACTERISTIC: {"UUID": track.UUID, "Service": svc}}}
        self.assertEqual(track.read_objects(objects, lambda *_: packet(), reader)["squal"], 90)
        objects[svc][reader.SERVICE]["UUID"] = "other"
        self.assertEqual(track.read_objects(objects, lambda *_: self.fail("wrong service"), reader)["state"], "unsupported")
        objects[dev][reader.DEVICE]["Connected"] = False
        self.assertEqual(track.read_objects(objects, lambda *_: self.fail("disconnected"), reader)["state"], "disconnected")


if __name__ == "__main__":
    unittest.main()
