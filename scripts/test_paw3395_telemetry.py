import sqlite3
import unittest

import trackball_telemetry as track
import trackball_usb as usb
from test_trackball_telemetry import packet


def paw_packet(**changes):
    return packet(version=2, reserved=1, stage=3, **changes) + bytes((73, 200, 9, 0xa5))


class PawQualityTest(unittest.TestCase):
    def test_new_frame_decodes_twelve_bit_exposure_and_raw_values(self):
        value = usb.parse_line('KEYBOARD_TB2 1234 ' + paw_packet(shutter=4095).hex())
        self.assertEqual(value['sensor'], 'paw3395')
        self.assertEqual(value['shutter'], 4095)
        self.assertEqual((value['raw_sum'], value['raw_max'], value['raw_min'], value['observation']),
                         (73, 200, 9, 0xa5))
        self.assertIsNone(usb.parse_line('KEYBOARD_TB1 1234 ' + paw_packet().hex()))

    def test_wrong_length_sensor_id_and_shutter_are_rejected(self):
        for data in (paw_packet()[:-1], paw_packet(shutter=4096), packet(version=2, reserved=1),
                     packet(version=2, reserved=2) + b'\0'*4, packet() + b'\0'*4):
            with self.subTest(data=data), self.assertRaises(ValueError):
                track.decode(data)

    def test_zero_is_valid_and_errors_or_stale_samples_clear_all_optical_values(self):
        self.assertEqual(track.decode(paw_packet(squal=0, shutter=0))['squal'], 0)
        for changes in (dict(age=31), dict(error=-5), dict(flags=2)):
            value = track.decode(paw_packet(**changes))
            for key in ('squal', 'shutter', 'raw_sum', 'raw_max', 'raw_min', 'observation'):
                self.assertIsNone(value[key])

    def test_legacy_history_is_preserved_and_paw_metrics_use_separate_series(self):
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE trackball_samples (timestamp REAL PRIMARY KEY, connected INTEGER, state TEXT NOT NULL, ready INTEGER, squal INTEGER, shutter INTEGER, source_age_seconds INTEGER, sequence INTEGER, read_errors INTEGER, init_retries INTEGER, init_stage INTEGER, last_error INTEGER, motion INTEGER, error TEXT)')
        db.execute("INSERT INTO trackball_samples (timestamp,state,squal) VALUES (1,'ok',99)")
        track.initialize(db)
        track.initialize(db)
        legacy = dict(db.execute('SELECT * FROM trackball_samples').fetchone())
        self.assertEqual((legacy['sensor'], legacy['squal'], legacy['raw_sum']), ('pmw3610',99,None))
        row = track.collect(db, lambda: track.decode(paw_packet(shutter=4095)), now=100)
        text = track.exposition(row, 100, transport='usb')
        self.assertIn('keyboard_trackball_squal{half="right",sensor="paw3395"} 90', text)
        self.assertIn('keyboard_trackball_raw_sum{half="right",sensor="paw3395"} 73', text)
        self.assertIn('keyboard_trackball_shutter_cycles{half="right",sensor="paw3395"} 4095', text)
        self.assertEqual(track.latest(db)['sensor'], 'paw3395')
        self.assertEqual(db.execute('SELECT count(*) FROM trackball_samples').fetchone()[0],2)


if __name__ == '__main__':
    unittest.main()
