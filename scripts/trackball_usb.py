#!/usr/bin/env python3
"""Read only structured trackball diagnostics from an exact USB device serial."""
import argparse
from array import array
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import select
import sys
import termios
import time

from trackball_telemetry import decode

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
FRAME = re.compile(r"\bPEAMK_TB1 ([0-9]{1,10}) ([0-9a-fA-F]{40})$")


def parse_line(line):
    line = ANSI.sub("", line.decode("ascii", errors="replace") if isinstance(line, bytes) else line).strip()
    match = FRAME.search(line)
    if not match:
        return None
    uptime = int(match[1])
    if uptime > 0xFFFFFFFF:
        return None
    try:
        return dict(decode(bytes.fromhex(match[2])), firmware_uptime_ms=uptime, transport="usb")
    except ValueError:
        return None


class LiveFrames:
    """Discard startup buffers; require advancing uptime at the observed cadence.

    This detects bursts of queued logs and resets. Source age belongs to the
    snapshot at emission, not an independently measured USB transport latency.
    """

    def __init__(self, opened):
        self.opened = opened
        self.previous = None

    def accept(self, line, now):
        value = parse_line(line)
        if value is None:
            return None
        previous, self.previous = self.previous, (now, value["firmware_uptime_ms"])
        if previous is None or now - self.opened < 4:
            return None
        elapsed = now - previous[0]
        advanced = ((value["firmware_uptime_ms"] - previous[1]) & 0xFFFFFFFF) / 1000
        if elapsed < 0.5 or advanced == 0 or abs(advanced - elapsed) > 1:
            return None
        return value


def frames(fd, seconds, clock=time.monotonic, stop=None):
    opened = clock()
    live = LiveFrames(opened)
    pending = b""
    discard_line = False
    while clock() - opened < seconds and (stop is None or not stop.is_set()):
        ready, _, _ = select.select([fd], [], [], min(0.5, max(0, seconds - (clock() - opened))))
        if not ready:
            continue
        chunk = os.read(fd, 4096)
        if not chunk:
            raise ConnectionError("USB console disconnected")
        if discard_line:
            _, separator, chunk = chunk.partition(b"\n")
            if not separator:
                continue
            discard_line = False
        pending += chunk
        while b"\n" in pending:
            line, pending = pending.split(b"\n", 1)
            value = live.accept(line, clock())
            if value is not None:
                yield value
        if len(pending) > 8192:
            pending = b""
            discard_line = True


def find_port(serial, directory=Path("/dev/serial/by-id")):
    if not re.fullmatch(r"[A-Za-z0-9]+", serial):
        raise ValueError("USB serial must contain only letters and digits")
    ports = [directory / f"usb-ZMK_Project_{name}_{serial}-if00"
             for name in ("darkblue", "roseblue", "PeaMK")]
    matches = [port for port in ports if port.exists()]
    if not matches:
        raise FileNotFoundError(f"USB diagnostics console absent for serial {serial}")
    if len(matches) != 1:
        raise ValueError(f"Multiple USB diagnostics consoles for serial {serial}")
    return matches[0]


def capture(port, serial, seconds, stop=None):
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    original = None
    controls_set = False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        original = termios.tcgetattr(fd)
        attrs = termios.tcgetattr(fd)
        attrs[0] = attrs[1] = attrs[3] = 0
        attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL | termios.HUPCL
        attrs[4] = attrs[5] = termios.B115200  # Never use 1200-baud bootloader touch.
        attrs[6][termios.VMIN] = attrs[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        termios.tcflush(fd, termios.TCIFLUSH)
        fcntl.ioctl(fd, termios.TIOCMBIS, array("i", [termios.TIOCM_DTR | termios.TIOCM_RTS]))
        controls_set = True
        for value in frames(fd, seconds, stop=stop):
            yield dict(value, usb_serial=serial, received_at=datetime.now(timezone.utc).isoformat())
    finally:
        try:
            if controls_set:
                fcntl.ioctl(fd, termios.TIOCMBIC, array("i", [termios.TIOCM_DTR | termios.TIOCM_RTS]))
            if original is not None:
                termios.tcsetattr(fd, termios.TCSANOW, original)
        except (OSError, termios.error):
            pass  # The device may have been unplugged.
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True, help="Select the physical keyboard by USB serial")
    parser.add_argument("--port", type=Path, help="Explicit console path, for an isolated device mapping")
    parser.add_argument("--seconds", type=int, default=30)
    args = parser.parse_args()
    if args.seconds < 6:
        parser.error("capture must last at least six seconds to discard buffered logs")
    try:
        port = args.port if args.port is not None else find_port(args.serial)
        count = 0
        for value in capture(port, args.serial, args.seconds):
            print(json.dumps(value), flush=True)
            count += 1
        if not count:
            print("No live USB quality frames received. The selected right half needs the USB-quality "
                  "debug build; ordinary debug logs do not contain these frames.", file=sys.stderr)
            return 2
    except (OSError, ValueError, ConnectionError, termios.error) as error:
        print(f"USB quality reader: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
