from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import trackball_usb as usb
import trackball_usb_monitor as monitor
from test_trackball_usb import line


class UsbMonitorTest(unittest.TestCase):
    def test_renamed_device_keeps_exact_serial_and_excludes_bootloader(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "usb-ZMK_Project_darkblue_FIRST-if00").touch()
            rose = root / "usb-ZMK_Project_roseblue_SECOND-if00"
            rose.touch()
            (root / "usb-Cosmos_Keyboards_Lemon_Wireless_THIRD-if00").touch()
            self.assertEqual(usb.find_port("SECOND", root), rose)
            with self.assertRaises(FileNotFoundError):
                usb.find_port("THIRD", root)
            with self.assertRaises(FileNotFoundError):
                usb.find_port("MISSING", root)

    def test_no_frames_stale_data_and_disconnect_never_repeat_old_quality(self):
        cache = monitor.Snapshot()
        cache.update(usb.parse_line(line(1000, age=29)), now=100)
        self.assertEqual(cache.read(now=101)["squal"], 110)
        self.assertEqual(cache.read(now=102)["state"], "stale")
        self.assertIsNone(cache.read(now=102)["squal"])
        self.assertEqual(cache.read(now=109)["state"], "unavailable")
        self.assertNotIn("squal", cache.read(now=109))
        cache.update(dict(connected=False, state="disconnected"), now=110)
        self.assertFalse(cache.read(now=111)["connected"])
        cache.update(usb.parse_line(line(500, squal=0)), now=112)
        self.assertEqual(cache.read(now=113)["squal"], 0)

    def test_history_rejects_another_device_and_keeps_reboots(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "roseblue.sqlite3"
            db = monitor.database(path, "SECOND", "roseblue")
            try:
                cache = monitor.Snapshot()
                cache.update(usb.parse_line(line(100000, sequence=500)))
                monitor.collect(db, cache, now=100)
                cache.update(usb.parse_line(line(1000, sequence=1, squal=0)))
                monitor.collect(db, cache, now=110)
                self.assertEqual(db.execute("SELECT count(*) FROM trackball_samples").fetchone()[0], 2)
                self.assertEqual(db.execute("SELECT firmware_uptime_ms FROM usb_frames ORDER BY timestamp DESC").fetchone()[0], 1000)
                text = monitor.metrics(db, "SECOND", "roseblue", 10, now=110)
                self.assertIn('peamk_trackball_usb_connected{half="right"} 1', text)
                self.assertIn('usb_serial="SECOND"', text)
                self.assertNotIn("bluetooth_connected", text)
                self.assertIn('peamk_trackball_squal{half="right"} 0', text)
                self.assertIn('peamk_trackball_squal{half="right"} NaN',
                              monitor.metrics(db, "SECOND", "roseblue", 10, now=131))
                with self.assertRaisesRegex(ValueError, "another USB device"):
                    monitor.database(path, "FIRST", "darkblue")
                self.assertEqual(db.execute("SELECT count(*) FROM trackball_samples").fetchone()[0], 2)
            finally:
                db.close()

    def test_missing_console_reconnects_without_switching_devices(self):
        stop = threading.Event()
        cache = monitor.Snapshot()
        def capture(port, serial, seconds, stop):
            self.assertEqual((port, serial), (Path("/dev/rose"), "SECOND"))
            yield usb.parse_line(line(1000))
            stop.set()
        with patch.object(usb, "find_port", side_effect=[FileNotFoundError(), Path("/dev/rose")]) as find, \
             patch.object(usb, "capture", side_effect=capture), \
             patch.object(monitor, "device_present", return_value=True), \
             patch.object(stop, "wait", return_value=False):
            monitor.receive("SECOND", cache, stop)
        self.assertEqual([call.args for call in find.call_args_list], [("SECOND",), ("SECOND",)])
        self.assertEqual(cache.read()["squal"], 110)

    def test_serial_setup_disconnect_retries_and_then_recovers(self):
        stop = threading.Event()
        cache = monitor.Snapshot()
        def success(*_, **kwargs):
            yield usb.parse_line(line(2000))
            stop.set()
        with patch.object(usb, "find_port", return_value=Path("/dev/rose")), \
             patch.object(usb, "capture", side_effect=[usb.termios.error(5, "disconnected"), success()]) as capture, \
             patch.object(monitor, "device_present", return_value=True), \
             patch.object(stop, "wait", return_value=False):
            monitor.receive("SECOND", cache, stop)
        self.assertEqual(capture.call_count, 2)
        self.assertEqual(cache.read()["squal"], 110)


if __name__ == "__main__":
    unittest.main()
