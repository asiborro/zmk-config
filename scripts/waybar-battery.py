#!/usr/bin/env python3
"""Read PeaMK's two battery services through its existing BlueZ connection."""

import argparse
import json
import sys

DEVICE = "org.bluez.Device1"
SERVICE = "org.bluez.GattService1"
CHARACTERISTIC = "org.bluez.GattCharacteristic1"
DESCRIPTOR = "org.bluez.GattDescriptor1"
BATTERY_SERVICE = "0000180f-0000-1000-8000-00805f9b34fb"
BATTERY_LEVEL = "00002a19-0000-1000-8000-00805f9b34fb"
USER_DESCRIPTION = "00002901-0000-1000-8000-00805f9b34fb"


class ReadError(Exception):
    """A Bluetooth value is temporarily unavailable."""


def battery_levels(objects, read_value, device_name="PeaMK"):
    """Identify sides by ZMK's descriptor, never by GATT handle order."""
    levels = {"connected": False, "left": None, "right": None, "proxy": False}
    device_path = next(
        (
            path
            for path, interfaces in objects.items()
            if interfaces.get(DEVICE, {}).get("Connected")
            and interfaces[DEVICE].get("Alias", interfaces[DEVICE].get("Name")) == device_name
        ),
        None,
    )
    if device_path is None:
        return levels
    levels["connected"] = True
    for path, interfaces in objects.items():
        char = interfaces.get(CHARACTERISTIC, {})
        if not path.startswith(device_path + "/") or char.get("UUID") != BATTERY_LEVEL:
            continue
        service = objects.get(char.get("Service"), {}).get(SERVICE, {})
        if service.get("UUID") != BATTERY_SERVICE:
            continue
        descriptions = [
            desc_path
            for desc_path, desc_interfaces in objects.items()
            if desc_interfaces.get(DESCRIPTOR, {}).get("Characteristic") == path
            and desc_interfaces[DESCRIPTOR].get("UUID") == USER_DESCRIPTION
        ]
        try:
            if descriptions:
                description = bytes(read_value(descriptions[0], DESCRIPTOR)).decode(
                    "utf-8", errors="replace"
                ).rstrip("\0")
                # This keyboard has exactly one split peripheral: the left half.
                if description != "Peripheral 0":
                    continue
                side = "left"
                levels["proxy"] = True
            else:
                side = "right"
            value = read_value(path, CHARACTERISTIC)
        except ReadError:
            continue
        if len(value) == 1 and 0 <= value[0] <= 100:
            levels[side] = value[0]
    return levels


def waybar_output(levels, device_name="PeaMK"):
    def percent(value):
        return "--" if value is None else f"{value}%"

    if not levels["connected"]:
        return {"text": "", "tooltip": f"{device_name} is disconnected", "class": "disconnected"}
    left, right = levels["left"], levels["right"]
    tooltip = f"{device_name}\nLeft: {percent(left)}\nRight: {percent(right)}"
    if left is None:
        if levels["proxy"]:
            tooltip += "\nWaiting for the left battery reading."
        else:
            tooltip += "\nThe left battery service is not available yet."
    tooltip += "\nReadings are relayed by the right half over Bluetooth."
    tooltip += "\nPercentages are estimated from battery voltage."
    available = [value for value in (left, right) if value is not None]
    return {
        "text": f"{device_name} L {percent(left)} R {percent(right)}",
        "tooltip": tooltip,
        "class": "warning" if available and min(available) <= 20 else "connected",
    }


def read_bluez(device_name):
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib

    bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    objects = bus.call_sync(
        "org.bluez", "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects",
        None, GLib.VariantType.new("(a{oa{sa{sv}}})"), Gio.DBusCallFlags.NONE, 3000, None,
    ).unpack()[0]

    def read_value(path, interface):
        try:
            return bus.call_sync(
                "org.bluez", path, interface, "ReadValue",
                GLib.Variant("(a{sv})", ({},)), GLib.VariantType.new("(ay)"),
                Gio.DBusCallFlags.NONE, 2000, None,
            ).unpack()[0]
        except GLib.Error as error:
            raise ReadError(str(error)) from error

    return battery_levels(objects, read_value, device_name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="PeaMK", help="Bluetooth device alias")
    args = parser.parse_args()
    try:
        output = waybar_output(read_bluez(args.device), args.device)
    except Exception as error:
        # Keep Waybar alive if BlueZ is restarting or the system bus is unavailable.
        print(f"Battery reader: {error}", file=sys.stderr)
        output = {
            "text": f"{args.device} L -- R --",
            "tooltip": "Battery readings are temporarily unavailable.",
            "class": "unavailable",
        }
    print(json.dumps(output))


if __name__ == "__main__":
    main()
