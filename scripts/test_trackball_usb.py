import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import trackball_telemetry as track
import trackball_usb as usb


def line(uptime, **changes):
    values = dict(version=1, flags=7, squal=110, retries=0, shutter=480, error=0,
                  age=0, sequence=42, errors=0, stage=4, reserved=0)
    values.update(changes)
    return f"[00:01:00.000,000] <inf> peamk_trackball_usb: PEAMK_TB1 {uptime} " + track.PACKET.pack(*values.values()).hex()


class UsbQualityTest(unittest.TestCase):
    def test_ansi_and_zero_values_use_the_same_decoder_as_bluetooth(self):
        result = usb.parse_line("\x1b[0m" + line(1234, squal=0, shutter=0) + "\x1b[0m\r\n")
        self.assertEqual(result['squal'], 0)
        self.assertEqual(result['shutter'], 0)
        self.assertEqual(result['firmware_uptime_ms'], 1234)
        self.assertEqual(result['transport'], 'usb')

    def test_unrelated_and_malformed_lines_are_never_reported(self):
        for text in ('key_pressed: 42', '<err> zmk: Failed to set output', line(5)[:-1],
                     line(5)+'00', line(5, version=2), line(5, shutter=512), line(2**32),
                     line(5).replace('PEAMK_TB1', 'PEAMK_TB2')):
            with self.subTest(text=text):
                self.assertIsNone(usb.parse_line(text))

    def test_backlog_warmup_and_duplicate_packets_are_rejected(self):
        live = usb.LiveFrames(0)
        self.assertIsNone(live.accept(line(1000), 0))
        self.assertIsNone(live.accept(line(3000), 2))
        self.assertEqual(live.accept(line(5000), 4)['squal'], 110)
        self.assertIsNone(live.accept(line(5000), 6))
        self.assertIsNone(live.accept(line(7000), 6.01))
        self.assertIsNone(live.accept(line(9000), 6.02))
        self.assertEqual(live.accept(line(11000), 8.02)['squal'], 110)

    def test_reboot_and_clock_wrap_need_a_live_sequence(self):
        live = usb.LiveFrames(0)
        live.accept(line(100000), 4)
        self.assertIsNone(live.accept(line(1000), 6))
        self.assertEqual(live.accept(line(3000), 8)['state'], 'ok')
        live.accept(line(0xFFFFFF00), 10)
        self.assertEqual(live.accept(line((0xFFFFFF00 + 2000) & 0xFFFFFFFF), 12)['state'], 'ok')

    def test_old_sensor_sample_and_spi_failure_remain_unavailable(self):
        for changes, state in ((dict(age=31), 'stale'), (dict(error=-5, errors=1), 'error'),
                               (dict(flags=2), 'no_sample')):
            result = usb.parse_line(line(1000, **changes))
            self.assertEqual(result['state'], state)
            self.assertIsNone(result['squal'])

    def test_fragmented_stream_ignores_other_debug_output(self):
        chunks = iter([line(1000).encode()[:20], line(1000).encode()[20:]+b'\n',
                       b'key_pressed: 42\n'+line(3000).encode()+b'\r\n', b''])
        clock = [0]
        def ready(*_):
            clock[0] += 2
            return [99], [], []
        with patch.object(usb.select, 'select', side_effect=ready), patch.object(usb.os, 'read', side_effect=lambda *_: next(chunks)):
            stream = usb.frames(99, 30, clock=lambda: clock[0])
            self.assertEqual(next(stream)['squal'], 110)
            with self.assertRaises(ConnectionError):
                next(stream)

    def test_oversize_partial_line_cannot_become_a_valid_frame(self):
        chunks = iter([b'x'*8193, line(1000).encode()+b'\n',
                       line(3000).encode()+b'\n', line(5000).encode()+b'\n', b''])
        clock = [0]
        def ready(*_):
            clock[0] += 2
            return [99], [], []
        with patch.object(usb.select, 'select', side_effect=ready), patch.object(usb.os, 'read', side_effect=lambda *_: next(chunks)):
            stream = usb.frames(99, 30, clock=lambda: clock[0])
            self.assertEqual(next(stream)['firmware_uptime_ms'], 5000)

    def test_device_selection_requires_the_exact_serial(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for serial in ('FIRST', 'SECOND'):
                (root / f'usb-ZMK_Project_PeaMK_{serial}-if00').touch()
            self.assertEqual(usb.find_port('SECOND', root).name, 'usb-ZMK_Project_PeaMK_SECOND-if00')
            with self.assertRaises(FileNotFoundError):
                usb.find_port('MISSING', root)
            with self.assertRaises(ValueError):
                usb.find_port('../SECOND', root)

    def test_busy_console_is_not_reconfigured_or_disconnected(self):
        with patch.object(usb.os, 'open', return_value=99), patch.object(usb.os, 'close') as close, \
             patch.object(usb.fcntl, 'flock', side_effect=BlockingIOError), patch.object(usb.fcntl, 'ioctl') as control:
            with self.assertRaises(BlockingIOError):
                list(usb.capture('/dev/example', 'SECOND', 6))
            control.assert_not_called()
            close.assert_called_once_with(99)

    def test_old_firmware_timeout_is_explicit_and_has_no_fake_readings(self):
        with patch.object(usb.sys, 'argv', ['trackball_usb.py','--serial','SECOND','--port','/dev/example','--seconds','6']), \
             patch.object(usb, 'capture', return_value=iter(())), contextlib.redirect_stdout(io.StringIO()) as out, \
             contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(usb.main(), 2)
            self.assertEqual(out.getvalue(), '')
            self.assertIn('No live USB quality frames', err.getvalue())


if __name__ == '__main__':
    unittest.main()
