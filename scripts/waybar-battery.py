#!/usr/bin/env python3
"""Read PeaMK's two battery services through its existing BlueZ connection."""

import argparse
import json
import hashlib
import os
from pathlib import Path
import struct
import sys

DEVICE = "org.bluez.Device1"
SERVICE = "org.bluez.GattService1"
CHARACTERISTIC = "org.bluez.GattCharacteristic1"
DESCRIPTOR = "org.bluez.GattDescriptor1"
BATTERY_SERVICE = "0000180f-0000-1000-8000-00805f9b34fb"
BATTERY_LEVEL = "00002a19-0000-1000-8000-00805f9b34fb"
USER_DESCRIPTION = "00002901-0000-1000-8000-00805f9b34fb"
TELEMETRY_SERVICE = "50ea0000-2f54-4e31-a2b7-6cb20f70a901"
TELEMETRY_LOCAL = "50ea0001-2f54-4e31-a2b7-6cb20f70a901"
TELEMETRY_PEER = "50ea0002-2f54-4e31-a2b7-6cb20f70a901"
MAX_SOURCE_AGE = 180
VOLTAGE_SETTLE_SECONDS = 120
BATTERY_MAX_MV = 4250
USB_MIN_MV = 4400
CURVE_PATH = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "peamk-battery/curve.json"


def voltage_percent(millivolts, points=None):
    """Interpolate a calibrated curve, or use ZMK's uncalibrated endpoints.

    Do not substitute another battery's discharge curve and call it calibrated.
    The endpoint estimate is bounded and avoids ZMK's integer/offset rounding.
    """
    points = points if points is not None else [[3450, 0], [4200, 100]]
    if len(points) < 2 or points[0][1] != 0 or points[-1][1] != 100:
        raise ValueError("Battery curve must start at 0% and end at 100%")
    for i, (mv, percent) in enumerate(points):
        if not 2500 <= mv <= 4300 or not 0 <= percent <= 100:
            raise ValueError("Battery curve point outside permitted range")
        if i and (mv <= points[i - 1][0] or percent <= points[i - 1][1]):
            raise ValueError("Battery curve points must increase in voltage and percentage")
    if millivolts <= points[0][0]:
        return 0.0
    for (lo_v, lo_p), (hi_v, hi_p) in zip(points, points[1:]):
        if millivolts <= hi_v:
            return round(lo_p + (millivolts - lo_v) * (hi_p - lo_p) / (hi_v - lo_v), 2)
    return 100.0


def decode_telemetry(value, points=None):
    if len(value) != 16:
        raise ValueError("Telemetry packet must be 16 bytes")
    version, flags, brightness, relay_age, mv, age, sequence, power_age, raw_mv = struct.unpack(
        "<BBBBHHIHH", bytes(value))
    if version not in (1, 2) or flags & ~0x3F or (brightness != 255 and brightness > 100):
        raise ValueError("Unsupported or malformed telemetry packet")
    connected = bool(flags & 32)
    fresh = connected and relay_age <= MAX_SOURCE_AGE
    valid = fresh and bool(flags & 1) and sequence != 0 and age <= MAX_SOURCE_AGE
    if flags & 1 and not (1500 <= mv <= 6000 and 1500 <= raw_mv <= 6000):
        raise ValueError("Invalid supply voltage")
    # Lemon v2's MS88SF21 module ties VBUS to VDDH. Protocol v1's VBUS
    # detector therefore also asserted on battery, and its transition age
    # is unusable. Infer power from fresh supply voltage on both versions;
    # v2 measures transitions using these same conservative thresholds.
    usb = (True if mv >= USB_MIN_MV else False if mv <= BATTERY_MAX_MV else None) if valid else None
    power_age = power_age if version == 2 and usb is not None else None
    leds = bool(flags & 8) if fresh and flags & 16 else None
    result = dict(version=version, connected=connected, source_age=age, relay_age=relay_age,
                  sequence=sequence, power_age=power_age, usb=usb, leds=leds,
                  power_source="voltage-inference", reported_usb=bool(flags & 2) if flags & 4 else None,
                  brightness=brightness if fresh and brightness != 255 else None,
                  voltage_mv=mv if valid else None, raw_voltage_mv=raw_mv if valid else None,
                  estimate=None, method=("custom-" + hashlib.sha256(json.dumps(points).encode()).hexdigest()[:12])
                         if points is not None else "voltage-linear")
    if not connected:
        state = "disconnected"
    elif not fresh:
        state = "stale"
    elif usb is True:
        state = "usb"
    elif not valid:
        state = "unavailable"
    elif usb is None:
        state = "uncertain"
    elif power_age is not None and power_age < VOLTAGE_SETTLE_SECONDS:
        state = "settling"
    else:
        state = "battery"
        result["estimate"] = voltage_percent(mv, points)
    result["state"] = state
    return result


def add_telemetry(levels, objects, read_value, device_path, curves=None):
    channels = {}
    for path, interfaces in objects.items():
        char = interfaces.get(CHARACTERISTIC, {})
        if not path.startswith(device_path + "/"):
            continue
        service = objects.get(char.get("Service"), {}).get(SERVICE, {})
        if service.get("UUID") != TELEMETRY_SERVICE:
            continue
        if char.get("UUID") in (TELEMETRY_LOCAL, TELEMETRY_PEER):
            channels["right" if char["UUID"] == TELEMETRY_LOCAL else "left"] = path
    if not channels:
        return levels
    levels["telemetry"] = {}
    for half in ("left", "right"):
        try:
            points = curves.get(half) if curves else None
            levels["telemetry"][half] = decode_telemetry(read_value(channels[half], CHARACTERISTIC), points)
        except (ReadError, ValueError, KeyError, TypeError, struct.error) as exc:
            levels["telemetry"][half] = {"state": "unavailable", "error": str(exc)}
    return levels


class ReadError(Exception):
    """A Bluetooth value is temporarily unavailable."""


def battery_levels(objects, read_value, device_name="PeaMK", curves=None, include_legacy=True):
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
    add_telemetry(levels, objects, read_value, device_path, curves)
    if not include_legacy and "telemetry" in levels:
        return levels
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
        return {"text": f"{device_name} L -- R --", "tooltip": f"{device_name} is disconnected", "class": "disconnected"}
    if "telemetry" in levels:
        labels, detail, estimates = {}, [device_name], []
        for half in ("left", "right"):
            data = levels["telemetry"][half]
            state = data["state"]
            estimate = data.get("estimate")
            labels[half] = ("USB" if state == "usb" else
                            "…" if state == "settling" else
                            f"{round(estimate)}%" if estimate is not None else "--")
            line = f"{half.capitalize()}: {labels[half]}"
            if data.get("voltage_mv") is not None:
                line += f" · supply {data['voltage_mv'] / 1000:.3f} V"
            if state == "usb":
                line += " · USB inferred · charge level/completion unknown"
            elif state != "battery":
                line += f" · {state}"
            if data.get("source_age") is not None:
                line += f" · sample {data['source_age']} s old"
            detail.append(line)
            if estimate is not None:
                estimates.append(estimate)
        detail.append("Power and charge are voltage-based estimates; USB supply voltage does not measure the cell.")
        if any(data.get("version") == 1 for data in levels["telemetry"].values()):
            detail.append("Older telemetry: readings can lag by two minutes; unplug settling time is unknown.")
        return {"text": f"{device_name} L {labels['left']} R {labels['right']}",
                "tooltip": "\n".join(detail),
                "class": "warning" if estimates and min(estimates) <= 20 else "connected"}
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


def bluez_access():
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

    return objects, read_value


def read_bluez(device_name, include_legacy=True):
    objects, read_value = bluez_access()
    curves = None
    if CURVE_PATH.exists():
        curves = json.loads(CURVE_PATH.read_text())
    return battery_levels(objects, read_value, device_name, curves, include_legacy)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="PeaMK", help="Bluetooth device alias")
    args = parser.parse_args()
    try:
        output = waybar_output(read_bluez(args.device, include_legacy=False), args.device)
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
