#!/usr/bin/env python3
"""Continuously collect one USB trackball into its own SQLite history and metrics."""
import argparse
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import signal
import sqlite3
import threading
import time

import trackball_telemetry as track
import trackball_usb as usb

FRAME_TIMEOUT = 8  # Firmware emits every two seconds; never reuse a dead console.


class Snapshot:
    def __init__(self):
        self.lock = threading.Lock()
        self.value = dict(connected=False, state="disconnected")
        self.received = None

    def update(self, value, now=None):
        with self.lock:
            self.value = dict(value)
            self.received = time.monotonic() if now is None else now

    def read(self, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            value, received = dict(self.value), self.received
        if "firmware_uptime_ms" not in value:
            return value
        elapsed = max(0, now - received)
        if elapsed > FRAME_TIMEOUT:
            return dict(connected=True, state="unavailable", error="USB diagnostic frames timed out")
        if value.get("source_age_seconds") is not None:
            value["source_age_seconds"] += elapsed
            if value["state"] == "ok" and value["source_age_seconds"] > track.MAX_SAMPLE_AGE:
                value.update(state="stale", squal=None, shutter=None, motion=None)
        value["frame_age_seconds"] = elapsed
        return value


def device_present(serial, root=Path("/sys/bus/usb/devices")):
    for path in root.glob("*/serial"):
        try:
            if path.read_text().strip() == serial:
                return True
        except OSError:
            continue
    return False


def receive(serial, cache, stop):
    while not stop.is_set():
        try:
            port = usb.find_port(serial)
            cache.update(dict(connected=True, state="waiting"))
            for value in usb.capture(port, serial, float("inf"), stop=stop):
                cache.update(value)
        except (OSError, ValueError, ConnectionError, usb.termios.error) as exc:
            present = device_present(serial)
            state = "no_console" if isinstance(exc, FileNotFoundError) and present else (
                "unavailable" if present else "disconnected")
            cache.update(dict(connected=present, state=state, error=str(exc)))
        stop.wait(2)


def database(path, serial, keyboard):
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE IF NOT EXISTS usb_device (id INTEGER PRIMARY KEY CHECK(id=1), serial TEXT NOT NULL, keyboard TEXT NOT NULL)")
    existing = connection.execute("SELECT serial, keyboard FROM usb_device WHERE id=1").fetchone()
    if existing and (existing["serial"], existing["keyboard"]) != (serial, keyboard):
        connection.close()
        raise ValueError("This history belongs to another USB device; use a separate database")
    with connection:
        connection.execute("INSERT OR IGNORE INTO usb_device VALUES (1, ?, ?)", (serial, keyboard))
        connection.execute("""CREATE TABLE IF NOT EXISTS usb_frames (
            timestamp REAL PRIMARY KEY, firmware_uptime_ms INTEGER,
            received_at TEXT, frame_age_seconds REAL)""")
    track.initialize(connection)
    return connection


def collect(connection, cache, now=None):
    value = cache.read()
    row = track.collect(connection, lambda: value, now)
    with connection:
        connection.execute("INSERT INTO usb_frames VALUES (?, ?, ?, ?)", (
            row["timestamp"], value.get("firmware_uptime_ms"),
            value.get("received_at"), value.get("frame_age_seconds")))
    return row


def metrics(connection, serial, keyboard, interval, now=None):
    now = time.time() if now is None else now
    row = track.latest(connection)
    body = track.exposition(row, now, interval * 2, transport="usb")
    identity = ",".join(f"{key}={json.dumps(value)}" for key, value in
                        (("keyboard", keyboard), ("usb_serial", serial), ("transport", "usb")))
    body += '# HELP trackball_device_info Physical device assigned to this collector.\n'
    body += '# TYPE trackball_device_info gauge\n'
    body += f"trackball_device_info{{{identity}}} 1\n"
    return body


def serve(args):
    # Validate identity before serving or reading a console.
    connection = database(args.database, args.serial, args.keyboard)
    stop, cache = threading.Event(), Snapshot()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ("/", "/metrics"):
                self.send_error(404)
                return
            with closing(sqlite3.connect(args.database, timeout=10)) as db:
                db.row_factory = sqlite3.Row
                body = metrics(db, args.serial, args.keyboard, args.interval).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer((args.bind, args.port), Handler)
    reader = threading.Thread(target=receive, args=(args.serial, cache, stop), daemon=True)
    reader.start()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    previous = None
    try:
        while not stop.is_set():
            if not reader.is_alive():
                raise RuntimeError("USB reader stopped unexpectedly")
            row = collect(connection, cache)
            if row["state"] != previous:
                print(json.dumps(dict(keyboard=args.keyboard, usb_serial=args.serial,
                                      state=row["state"], error=row["error"])), flush=True)
                previous = row["state"]
            stop.wait(args.interval)
    finally:
        stop.set()
        reader.join(timeout=3)
        server.shutdown()
        server.server_close()
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--keyboard", default="roseblue")
    parser.add_argument("--database", type=Path, default=Path.home() / ".local/share/roseblue/history.sqlite3")
    parser.add_argument("--interval", type=int, default=10)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=19109)
    args = parser.parse_args()
    if not usb.re.fullmatch(r"[A-Za-z0-9]+", args.serial):
        parser.error("USB serial must contain only letters and digits")
    if args.interval < 5:
        parser.error("Collection interval must be at least five seconds")
    serve(args)


if __name__ == "__main__":
    main()
