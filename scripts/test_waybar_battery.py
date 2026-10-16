import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("battery", Path(__file__).with_name("waybar-battery.py"))
battery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(battery)


class BatteryReadingsTest(unittest.TestCase):
    def setUp(self):
        self.device = "/org/bluez/hci0/dev_test"
        self.left = self.device + "/service0001/char0002"
        self.right = self.device + "/service0040/char0041"
        self.desc = self.left + "/desc0005"
        self.objects = {
            self.device: {battery.DEVICE: {"Alias": "PeaMK", "Connected": True}},
            self.left: {battery.CHARACTERISTIC: {
                "UUID": battery.BATTERY_LEVEL, "Service": self.left.rsplit("/", 1)[0]}},
            self.right: {battery.CHARACTERISTIC: {
                "UUID": battery.BATTERY_LEVEL, "Service": self.right.rsplit("/", 1)[0]}},
            self.desc: {battery.DESCRIPTOR: {
                "UUID": battery.USER_DESCRIPTION, "Characteristic": self.left}},
        }
        for char in (self.left, self.right):
            self.objects[char.rsplit("/", 1)[0]] = {
                battery.SERVICE: {"UUID": battery.BATTERY_SERVICE}}
        self.values = {self.left: [82], self.right: [49], self.desc: list(b"Peripheral 0")}

    def read(self, path, _interface):
        value = self.values[path]
        if isinstance(value, Exception):
            raise value
        return value

    def test_descriptor_identifies_left_even_when_its_handle_comes_first(self):
        levels = battery.battery_levels(self.objects, self.read)
        self.assertEqual((levels["left"], levels["right"]), (82, 49))

    def test_old_firmware_leaves_left_unknown(self):
        del self.objects[self.left]
        levels = battery.battery_levels(self.objects, self.read)
        self.assertEqual(battery.waybar_output(levels)["text"], "PeaMK L -- R 49%")

    def test_unreadable_descriptor_does_not_replace_right_with_left(self):
        self.values[self.desc] = battery.ReadError("Disconnected during discovery")
        levels = battery.battery_levels(self.objects, self.read)
        self.assertIsNone(levels["left"])
        self.assertEqual(levels["right"], 49)

    def test_missing_or_invalid_samples_are_not_reported_as_zero(self):
        self.values[self.left] = [255]
        self.values[self.right] = []
        levels = battery.battery_levels(self.objects, self.read)
        self.assertEqual(battery.waybar_output(levels)["text"], "PeaMK L -- R --")

    def test_valid_empty_and_full_batteries_are_preserved(self):
        self.values[self.left] = [0]
        self.values[self.right] = [100]
        levels = battery.battery_levels(self.objects, self.read)
        self.assertEqual(battery.waybar_output(levels)["text"], "PeaMK L 0% R 100%")

    def test_other_peripheral_is_not_mislabeled_as_left(self):
        self.values[self.desc] = list(b"Peripheral 1")
        levels = battery.battery_levels(self.objects, self.read)
        self.assertIsNone(levels["left"])
        self.assertEqual(levels["right"], 49)

    def test_disconnected_keyboard_does_not_read_stale_values(self):
        self.objects[self.device][battery.DEVICE]["Connected"] = False
        levels = battery.battery_levels(self.objects, lambda *_: self.fail("Unexpected read"))
        self.assertEqual(battery.waybar_output(levels)["text"], "")


if __name__ == "__main__":
    unittest.main()
