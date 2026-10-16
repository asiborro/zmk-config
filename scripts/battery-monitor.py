#!/usr/bin/env python3
"""Log both PeaMK batteries every five minutes and export Prometheus metrics.

New firmware supplies voltage, USB, RGB and sample age for both halves.
Older firmware uses manual annotations and its original percentage readings.
"""

import argparse
import csv
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import signal
import sqlite3
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HALVES = ("left", "right")
INTERVAL = 300
SETTLE_SECONDS = 600
DEFAULT_DATABASE = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "peamk-battery/history.sqlite3"


def database(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS conditions (
            half TEXT PRIMARY KEY,
            leds TEXT NOT NULL DEFAULT 'unknown',
            power TEXT NOT NULL DEFAULT 'unknown',
            brightness INTEGER,
            capacity_mah INTEGER NOT NULL DEFAULT 750,
            since REAL NOT NULL DEFAULT 0
        );
        INSERT OR IGNORE INTO conditions (half) VALUES ('left'), ('right');
        CREATE TABLE IF NOT EXISTS events (
            timestamp REAL NOT NULL, half TEXT NOT NULL,
            conditions TEXT NOT NULL, note TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS samples (
            timestamp REAL NOT NULL, half TEXT NOT NULL,
            connected INTEGER, level INTEGER, error TEXT,
            leds TEXT NOT NULL, power TEXT NOT NULL, brightness INTEGER,
            capacity_mah INTEGER NOT NULL, condition_since REAL NOT NULL,
            eligible INTEGER NOT NULL,
            PRIMARY KEY (timestamp, half)
        );
    """)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(samples)")}
    additions = {
        "telemetry": "INTEGER NOT NULL DEFAULT 0",
        "voltage_mv": "INTEGER", "raw_voltage_mv": "INTEGER",
        "source_age_seconds": "INTEGER", "relay_age_seconds": "INTEGER",
        "sequence": "INTEGER", "power_age_seconds": "INTEGER",
        "estimated_level": "REAL", "estimate_method": "TEXT",
        "reporting_state": "TEXT", "power_source": "TEXT", "leds_source": "TEXT",
    }
    with connection:
        for name, definition in additions.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE samples ADD COLUMN {name} {definition}")
    return connection


def mark(connection, halves, changes, note="", now=None):
    now = time.time() if now is None else now
    with connection:
        for half in halves:
            state = dict(connection.execute("SELECT * FROM conditions WHERE half=?", (half,)).fetchone())
            state.update(changes)
            if changes.get("leds") == "off":
                state["brightness"] = 0
            elif changes.get("leds") == "on" and "brightness" not in changes and state["brightness"] == 0:
                state["brightness"] = None
            state["since"] = now
            connection.execute("""UPDATE conditions SET leds=:leds, power=:power,
                brightness=:brightness, capacity_mah=:capacity_mah, since=:since WHERE half=:half""", state)
            connection.execute("INSERT INTO events VALUES (?, ?, ?, ?)", (now, half, json.dumps(state), note))


def read_keyboard(device):
    spec = importlib.util.spec_from_file_location("waybar_battery", Path(__file__).with_name("waybar-battery.py"))
    reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reader)
    return reader.read_bluez(device)


def collect(connection, reader, now=None):
    states = [dict(row) for row in connection.execute("SELECT * FROM conditions ORDER BY half")]
    previous = {row["half"]: row for row in latest(connection)}
    error = None
    try:
        levels = reader()
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        levels = {"connected": None, "left": None, "right": None}
    now = time.time() if now is None else now
    rows = []
    with connection:
        for state in states:
            level = levels[state["half"]] if levels["connected"] else None
            if level is not None and (type(level) is not int or not 0 <= level <= 100):
                level = None
            telemetry = levels.get("telemetry", {}).get(state["half"])
            extra = dict(telemetry=int(telemetry is not None), voltage_mv=None, raw_voltage_mv=None,
                         source_age_seconds=None, relay_age_seconds=None, sequence=None,
                         power_age_seconds=None, estimated_level=None, estimate_method="legacy",
                         reporting_state="unavailable", power_source="annotation", leds_source="annotation")
            if telemetry is not None:
                extra.update(voltage_mv=telemetry.get("voltage_mv"), raw_voltage_mv=telemetry.get("raw_voltage_mv"),
                             source_age_seconds=telemetry.get("source_age"), relay_age_seconds=telemetry.get("relay_age"),
                             sequence=telemetry.get("sequence"), power_age_seconds=telemetry.get("power_age"),
                             estimated_level=telemetry.get("estimate"), estimate_method=telemetry.get("method"),
                             reporting_state=telemetry["state"], power_source="firmware", leds_source="firmware")
                state["power"] = {True: "usb", False: "battery", None: "unknown"}[telemetry.get("usb")]
                state["leds"] = {True: "on", False: "off", None: "unknown"}[telemetry.get("leds")]
                state["brightness"] = telemetry.get("brightness")
                prior = previous.get(state["half"])
                same = prior and prior["telemetry"] and all(
                    prior[field] == state[field] for field in ("power", "leds", "brightness", "capacity_mah"))
                same = same and prior["estimate_method"] == extra["estimate_method"]
                same = same and now - prior["timestamp"] <= INTERVAL * 2.5
                if same and extra["sequence"] is not None and prior["sequence"] is not None:
                    same = extra["sequence"] >= prior["sequence"]
                state["since"] = prior["condition_since"] if same else now
                power_age = extra["power_age_seconds"]
                if power_age is not None and power_age < 65535 and now - power_age > state["since"] + 120:
                    state["since"] = now - power_age
            elif levels["connected"]:
                extra["reporting_state"] = state["power"] if level is not None else "unavailable"
                if state["power"] == "battery" and level is not None:
                    extra["estimated_level"] = level
            estimate = extra["estimated_level"]
            eligible = (estimate is not None and 0 < estimate < 100 and state["power"] == "battery"
                        and state["leds"] != "unknown" and now - state["since"] >= SETTLE_SECONDS
                        and extra["reporting_state"] == "battery")
            row = dict(timestamp=now, half=state["half"], connected=levels["connected"],
                       level=level, error=error, leds=state["leds"], power=state["power"],
                       brightness=state["brightness"], capacity_mah=state["capacity_mah"],
                       condition_since=state["since"], eligible=int(eligible), **extra)
            columns = ",".join(row)
            parameters = ",".join(":" + key for key in row)
            connection.execute(f"INSERT INTO samples ({columns}) VALUES ({parameters})", row)
            rows.append(row)
    return rows


def latest(connection):
    return [dict(row) for row in connection.execute(
        "SELECT * FROM samples WHERE timestamp=(SELECT MAX(timestamp) FROM samples) ORDER BY half"
    )]


def exposition(rows, now, max_age=INTERVAL * 2):
    lines = []

    def metric(name, description, values):
        lines.extend((f"# HELP {name} {description}", f"# TYPE {name} gauge"))
        for labels, value in values:
            suffix = "{" + ",".join(f"{key}={json.dumps(str(val))}" for key, val in labels.items()) + "}" if labels else ""
            if value is None:
                value = "NaN"
            elif isinstance(value, bool):
                value = int(value)
            lines.append(f"{name}{suffix} {value}")

    age = max(0, now - rows[0]["timestamp"]) if rows else None
    fresh = bool(rows) and age <= max_age
    metric("peamk_sample_age_seconds", "Age of the latest collection attempt, not of the left firmware sample.", [({}, age)])
    metric("peamk_collection_success", "One if a recent BlueZ collection completed without an exception.", [({}, fresh and not rows[0]["error"])])
    metric("peamk_bluetooth_connected", "Central connected to this Linux host; NaN when unknown.", [({}, rows[0]["connected"] if fresh else None)])
    metric("peamk_battery_read_success", "One if this half returned a valid value in the latest recent collection.",
           [({"half": row["half"]}, fresh and (row["reporting_state"] in ("battery", "usb", "settling")
            if row["telemetry"] else row["level"] is not None)) for row in rows])
    metric("peamk_battery_reported_ratio", "Raw voltage-derived estimate; USB may force 1; left may be stale in firmware.",
           [({"half": row["half"]}, row["level"] / 100) for row in rows if fresh and row["level"] is not None])
    metric("peamk_battery_discharge_ratio", "Annotated battery-only estimate after settling, excluding saturated 100 percent readings.",
           [({"half": row["half"], "leds": row["leds"]}, analysis_level(row) / 100)
            for row in rows if fresh and row["eligible"]])
    metric("peamk_discharge_sample_eligible", "One if the latest sample meets the annotated discharge comparison conditions.",
           [({"half": row["half"]}, fresh and bool(row["eligible"])) for row in rows])
    metric("peamk_test_condition_info", "Effective conditions; firmware values override annotations.",
           [({"half": row["half"], "leds": row["leds"], "power": row["power"]}, 1) for row in rows])
    metric("peamk_usb_power", "USB power: 1 present, 0 battery, NaN unknown; not charge completion.",
           [({"half": row["half"]}, {"usb": 1, "battery": 0}.get(row["power"])) for row in rows])
    metric("peamk_leds_enabled", "RGB state: 1 on, 0 off, NaN unknown.",
           [({"half": row["half"]}, {"on": 1, "off": 0}.get(row["leds"])) for row in rows])
    metric("peamk_led_brightness_ratio", "User-annotated brightness; NaN when unknown.",
           [({"half": row["half"]}, None if row["brightness"] is None else row["brightness"] / 100) for row in rows])
    metric("peamk_battery_capacity_coulombs", "User-annotated nominal battery capacity (1 mAh = 3.6 coulombs).",
           [({"half": row["half"]}, row["capacity_mah"] * 3.6) for row in rows])
    metric("peamk_condition_since_timestamp_seconds", "Time the current test conditions were annotated.",
           [({"half": row["half"]}, row["condition_since"]) for row in rows])
    for name, description, field, scale in (
        ("peamk_supply_voltage_volts", "Median of five ADC readings; USB measures supply, not cell voltage.", "voltage_mv", 1000),
        ("peamk_supply_raw_voltage_volts", "Last unfiltered ADC supply reading.", "raw_voltage_mv", 1000),
        ("peamk_voltage_sample_age_seconds", "Age of the ADC sample, including split relay delay.", "source_age_seconds", 1),
        ("peamk_telemetry_relay_age_seconds", "Age of power and RGB metadata at the central.", "relay_age_seconds", 1),
    ):
        metric(name, description, [({"half": row["half"]}, row[field] / scale)
                                   for row in rows if fresh and row[field] is not None])
    metric("peamk_battery_estimated_ratio", "Voltage estimate; absent on USB, stale data or after a power transition.",
           [({"half": row["half"], "method": row["estimate_method"]}, row["estimated_level"] / 100)
            for row in rows if fresh and row["estimated_level"] is not None])
    metric("peamk_reporting_state_info", "Reporting state; USB does not imply a full battery.",
           [({"half": row["half"], "state": row["reporting_state"] or "legacy"}, 1)
            for row in rows if fresh])
    metric("peamk_condition_source_info", "Source of power and RGB values: firmware or manual annotations.",
           [({"half": row["half"], "source": row["power_source"] or "annotation"}, 1)
            for row in rows if fresh])
    return "\n".join(lines) + "\n"


def analysis_level(row):
    return row["estimated_level"] if row.get("estimated_level") is not None else row["level"]


def segments(rows, max_gap=INTERVAL * 2.5):
    """Never measure a drop across charging, missing data, or changed conditions."""
    result = []
    for half in HALVES:
        segment = []
        for row in (row for row in rows if row["half"] == half):
            if not row["eligible"]:
                if segment:
                    result.append(segment)
                segment = []
                continue
            if segment:
                previous = segment[-1]
                if (row["condition_since"] != previous["condition_since"]
                        or row["timestamp"] - previous["timestamp"] > max_gap
                        or analysis_level(row) > analysis_level(previous) + 3):
                    result.append(segment)
                    segment = []
            segment.append(row)
        if segment:
            result.append(segment)
    return result


def report(connection, hours, now=None):
    now = time.time() if now is None else now
    rows = [dict(row) for row in connection.execute(
        "SELECT * FROM samples WHERE timestamp>=? ORDER BY timestamp, half", (now - hours * 3600,)
    )]
    attempts = len({row["timestamp"] for row in rows})
    output = [f"Last {hours:g} hours: {attempts} collection attempts (normally 12/hour)."]
    for half in HALVES:
        side = [row for row in rows if row["half"] == half]
        output.append(f"{half.title()}: {sum(row['level'] is not None for row in side)} readings; "
                      f"{sum(row['eligible'] for row in side)} eligible for discharge comparison.")
    count = 0
    for group in segments(rows):
        first, last = group[0], group[-1]
        elapsed = (last["timestamp"] - first["timestamp"]) / 3600
        if elapsed < 1:
            continue
        count += 1
        drop = analysis_level(first) - analysis_level(last)
        start = datetime.fromtimestamp(first["timestamp"], timezone.utc).isoformat(timespec="minutes")
        brightness = "unknown" if first["brightness"] is None else f"{first['brightness']}%"
        output.append(f"{start} {first['half']}, LEDs {first['leds']} (brightness {brightness}), "
                      f"{first['capacity_mah']} mAh: {analysis_level(first):g}% -> {analysis_level(last):g}% "
                      f"in {elapsed:.2f} h ({drop / elapsed:.2f} percentage points/hour).")
    if not count:
        output.append("No uninterrupted, annotated battery-only period of at least one hour yet.")
    output.extend([
        "Percentages estimate voltage, not remaining mAh; short slopes cannot establish full runtime.",
        "Old firmware may cache the left value; telemetry exposes age and disconnects.",
        "For sizing, measure comparable full-charge-to-recharge runs with LEDs on and off.",
        "At the same load, candidate runtime is approximately measured runtime * candidate mAh / tested mAh.",
        "For 750 mAh tests: 1500 mAh is about 2x, 2000 mAh about 2.67x, 3000 mAh about 4x.",
    ])
    return "\n".join(output)


def serve(args):
    stop = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/metrics":
                self.send_error(404)
                return
            connection = database(args.database)
            try:
                body = exposition(latest(connection), time.time(), args.interval * 2).encode()
            finally:
                connection.close()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer((args.bind, args.port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    connection = database(args.database)
    try:
        while not stop.is_set():
            started = time.monotonic()
            rows = collect(connection, lambda: read_keyboard(args.device))
            print(json.dumps({"timestamp": rows[0]["timestamp"], "connected": rows[0]["connected"],
                              **{row["half"]: row["level"] for row in rows}, "error": rows[0]["error"]}), flush=True)
            # Do not backfill invented samples after suspend or a slow collection.
            stop.wait(max(0, args.interval - (time.monotonic() - started)))
    finally:
        connection.close()
        server.shutdown()
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    commands = parser.add_subparsers(dest="command", required=True)
    daemon = commands.add_parser("serve", help="Collect every five minutes and serve cached metrics")
    daemon.add_argument("--device", default="PeaMK")
    daemon.add_argument("--interval", type=int, default=INTERVAL)
    daemon.add_argument("--bind", default="127.0.0.1")
    daemon.add_argument("--port", type=int, default=19108)
    annotation = commands.add_parser("mark", help="Annotate a physical change; does not control the keyboard")
    annotation.add_argument("--half", choices=(*HALVES, "both"), default="both")
    annotation.add_argument("--leds", choices=("on", "off", "unknown"))
    annotation.add_argument("--power", choices=("battery", "usb", "unknown"))
    annotation.add_argument("--brightness", type=int)
    annotation.add_argument("--capacity-mah", type=int)
    annotation.add_argument("--note", default="")
    summary = commands.add_parser("report", help="Summarize the last day without treating USB readings as discharge")
    summary.add_argument("--hours", type=float, default=24)
    commands.add_parser("status", help="Show annotations and the latest samples")
    commands.add_parser("export", help="Export all stored samples as CSV")
    args = parser.parse_args()
    if args.command == "serve":
        if args.interval < 30:
            parser.error("interval must be at least 30 seconds")
        serve(args)
        return
    connection = database(args.database)
    try:
        if args.command == "mark":
            changes = {key: getattr(args, key) for key in ("leds", "power", "brightness", "capacity_mah")
                       if getattr(args, key) is not None}
            if args.brightness is not None and not 0 <= args.brightness <= 100:
                parser.error("brightness must be 0..100")
            if args.capacity_mah is not None and args.capacity_mah <= 0:
                parser.error("capacity must be positive")
            if not changes and not args.note:
                parser.error("provide a changed condition or a note to start a new test period")
            mark(connection, HALVES if args.half == "both" else (args.half,), changes, args.note)
            print("Conditions recorded. Apply changes on the keyboard yourself; next sample is within five minutes.")
        elif args.command == "report":
            if args.hours <= 0:
                parser.error("hours must be positive")
            print(report(connection, args.hours))
        elif args.command == "status":
            print(json.dumps({"conditions": [dict(row) for row in connection.execute("SELECT * FROM conditions")],
                              "latest": latest(connection)}, indent=2))
        elif args.command == "export":
            cursor = connection.execute("SELECT * FROM samples ORDER BY timestamp, half")
            writer = csv.writer(sys.stdout)
            writer.writerow([column[0] for column in cursor.description])
            writer.writerows(cursor)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
