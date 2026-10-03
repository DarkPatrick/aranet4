"""SQLite storage. Timestamps are unix seconds (UTC)."""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    ts          INTEGER PRIMARY KEY,
    co2         INTEGER,
    temperature REAL,
    humidity    REAL,
    pressure    REAL
);

CREATE TABLE IF NOT EXISTS device_status (
    ts       INTEGER PRIMARY KEY,
    name     TEXT,
    version  TEXT,
    battery  INTEGER,
    interval INTEGER
);
"""


@dataclass
class Reading:
    ts: int
    co2: int | None
    temperature: float | None
    humidity: float | None
    pressure: float | None


def connect_readonly(path: str) -> sqlite3.Connection:
    """Read-only connection for the dashboard. Never writes, so it is safe on a
    copy that is atomically replaced by sync. A missing file reads as empty."""
    if not Path(path).exists():
        return connect(":memory:")
    conn = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def connect(path: str) -> sqlite3.Connection:
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    # WAL lets the dashboard read while the collector writes
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def last_ts(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT MAX(ts) AS ts FROM readings").fetchone()
    return row["ts"]


def insert_readings(conn: sqlite3.Connection, readings: list[Reading], min_gap: int = 0) -> int:
    """Insert readings newer than the last stored one by more than `min_gap`
    seconds. History timestamps are derived from "seconds since last
    measurement", so the same point can come back a second or two off on the
    next run; the gap check stops it from being stored twice."""
    inserted = 0
    last = last_ts(conn)
    with conn:
        for r in sorted(readings, key=lambda r: r.ts):
            if last is not None and r.ts <= last + min_gap:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO readings (ts, co2, temperature, humidity, pressure)"
                " VALUES (?, ?, ?, ?, ?)",
                (r.ts, r.co2, r.temperature, r.humidity, r.pressure),
            )
            last = r.ts
            inserted += 1
    return inserted


def insert_status(conn, ts: int, name: str, version: str, battery: int | None, interval: int | None) -> None:
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO device_status (ts, name, version, battery, interval) VALUES (?, ?, ?, ?, ?)",
            (ts, name, version, battery, interval),
        )


def fetch_readings(conn, ts_from: int | None = None, ts_to: int | None = None) -> list[dict]:
    sql = "SELECT ts, co2, temperature, humidity, pressure FROM readings WHERE 1=1"
    args: list = []
    if ts_from is not None:
        sql += " AND ts >= ?"
        args.append(ts_from)
    if ts_to is not None:
        sql += " AND ts <= ?"
        args.append(ts_to)
    sql += " ORDER BY ts"
    return [dict(r) for r in conn.execute(sql, args)]


def latest(conn) -> dict:
    reading = conn.execute(
        "SELECT ts, co2, temperature, humidity, pressure FROM readings ORDER BY ts DESC LIMIT 1"
    ).fetchone()
    status = conn.execute(
        "SELECT ts, name, version, battery, interval FROM device_status ORDER BY ts DESC LIMIT 1"
    ).fetchone()
    bounds = conn.execute("SELECT MIN(ts) AS first, MAX(ts) AS last FROM readings").fetchone()
    return {
        "range": dict(bounds),
        "reading": dict(reading) if reading else None,
        "status": dict(status) if status else None,
    }
