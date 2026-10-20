"""Read passive PMW3610 diagnostics; no sensor commands or input data are sent."""
import importlib.util
import json
from pathlib import Path
import struct
import time

UUID = "50ea0003-2f54-4e31-a2b7-6cb20f70a901"
INTERVAL = 10
MAX_SAMPLE_AGE = 30
PACKET = struct.Struct("<BBBBHhHIIBB")


def decode(value):
    if len(value) != PACKET.size:
        raise ValueError("Trackball packet must be 20 bytes")
    version, flags, squal, retries, shutter, error, age, sequence, errors, stage, reserved = PACKET.unpack(bytes(value))
    if version != 1 or flags & ~7 or reserved or shutter > 511 or error > 0:
        raise ValueError("Unsupported or malformed trackball packet")
    valid, ready = bool(flags & 1), bool(flags & 2)
    state = ("error" if error else "not_ready" if not ready else
             "no_sample" if not valid else "stale" if age > MAX_SAMPLE_AGE else "ok")
    return dict(connected=True, state=state, ready=ready,
                squal=squal if state == "ok" else None,
                shutter=shutter if state == "ok" else None,
                source_age_seconds=age if valid else None, sequence=sequence,
                read_errors=errors, init_retries=retries, init_stage=stage,
                last_error=error, motion=bool(flags & 4) if state == "ok" else None)


def read_objects(objects, read_value, reader, device_name="PeaMK", address=None):
    device = reader.select_device(objects, device_name, address)
    if device is None:
        return dict(connected=False, state="disconnected")
    for path, interfaces in objects.items():
        char = interfaces.get(reader.CHARACTERISTIC, {})
        service = objects.get(char.get("Service"), {}).get(reader.SERVICE, {})
        if (path.startswith(device + "/") and char.get("UUID") == UUID
                and service.get("UUID") == reader.TELEMETRY_SERVICE):
            try:
                return decode(read_value(path, reader.CHARACTERISTIC))
            except (reader.ReadError, ValueError, TypeError, struct.error) as exc:
                return dict(connected=True, state="unavailable", error=str(exc))
    return dict(connected=True, state="unsupported")


def read_bluez(device_name="darkblue", address=None):
    spec = importlib.util.spec_from_file_location("battery_reader", Path(__file__).with_name("waybar-battery.py"))
    reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reader)
    objects, read_value = reader.bluez_access()
    return read_objects(objects, read_value, reader, device_name, address)


def initialize(connection):
    connection.execute("""CREATE TABLE IF NOT EXISTS trackball_samples (
        timestamp REAL PRIMARY KEY, connected INTEGER, state TEXT NOT NULL,
        ready INTEGER, squal INTEGER, shutter INTEGER, source_age_seconds INTEGER,
        sequence INTEGER, read_errors INTEGER, init_retries INTEGER, init_stage INTEGER,
        last_error INTEGER, motion INTEGER, error TEXT
    )""")
    connection.commit()


def collect(connection, reader=read_bluez, now=None):
    try:
        value = reader()
    except Exception as exc:
        value = dict(connected=None, state="unavailable", error=f"{type(exc).__name__}: {exc}")
    row = {key: value.get(key) for key in (
        "connected", "state", "ready", "squal", "shutter", "source_age_seconds",
        "sequence", "read_errors", "init_retries", "init_stage", "last_error", "motion", "error")}
    row["timestamp"] = time.time() if now is None else now
    with connection:
        connection.execute("INSERT INTO trackball_samples (" + ",".join(row) + ") VALUES (" +
                           ",".join(":" + key for key in row) + ")", row)
    return row


def latest(connection):
    row = connection.execute("SELECT * FROM trackball_samples ORDER BY timestamp DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def exposition(row, now, max_age=INTERVAL * 2, transport="bluetooth"):
    age = max(0, now - row["timestamp"]) if row else None
    fresh = row is not None and age <= max_age
    state = row["state"] if fresh else "unavailable"
    source_age = row["source_age_seconds"] + age if fresh and row["source_age_seconds"] is not None else None
    available = state == "ok" and source_age is not None and source_age <= MAX_SAMPLE_AGE
    if state == "ok" and not available:
        state = "stale"
    lines = []

    def metric(name, description, value, kind="gauge", extra=None):
        lines.extend((f"# HELP peamk_trackball_{name} {description}", f"# TYPE peamk_trackball_{name} {kind}"))
        labels = dict(half="right", **(extra or {}))
        suffix = ",".join(f"{key}={json.dumps(val)}" for key, val in labels.items())
        text = "NaN" if value is None else str(int(value)) if isinstance(value, bool) else str(value)
        lines.append(f"peamk_trackball_{name}{{{suffix}}} {text}")

    metric("collection_age_seconds", "Seconds since the latest diagnostics collection attempt.", age)
    metric("collection_success", "One when diagnostics were read recently; idle samples may be old.",
           fresh and state in ("ok", "stale", "no_sample", "not_ready", "error"))
    metric("supported", "One if firmware exposes diagnostics; NaN when not known.",
           False if state == "unsupported" else True if fresh and row["ready"] is not None else None)
    if transport not in ("bluetooth", "usb"):
        raise ValueError("Unknown diagnostics transport")
    metric(f"{transport}_connected", f"Host {transport} connection; NaN when unknown.", row["connected"] if fresh else None)
    metric("state_info", "Diagnostics state; stale may mean idle or stalled acquisition.", 1, extra={"state": state})
    metric("quality_available", "One for a recent successful sensor sample, including a legitimate zero SQUAL.", available)
    metric("squal", "Raw PMW3610 surface-quality register (0..255); not a percentage or distance.", row["squal"] if available else None)
    metric("shutter_cycles", "Raw nine-bit sensor exposure in clock cycles; not milliseconds.", row["shutter"] if available else None)
    metric("sample_age_seconds", "Age of last successful motion burst; includes collector cache age.", source_age)
    for name, field, help_text in (
        ("ready", "ready", "Sensor completed initialization."),
        ("last_error", "last_error", "Last initialization/read errno; zero after success."),
        ("init_retries", "init_retries", "Additional startup attempts in this boot."),
        ("init_stage", "init_stage", "Startup stage: 0..3 stages, 4 ready."),
    ):
        metric(name, help_text, row[field] if fresh else None)
    for name, field in (("bursts_total", "sequence"), ("read_errors_total", "read_errors")):
        if fresh and row[field] is not None:
            metric(name, "Count since firmware boot; resets on reboot and wraps at 2^32.", row[field], "counter")
    return "\n".join(lines) + "\n"
