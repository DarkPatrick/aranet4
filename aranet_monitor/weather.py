"""Cyprus Department of Meteorology automatic weather stations.

The open-data feed (one XML, all ~55 stations, 10-minute averages, refreshed
every 10 minutes) carries only the latest value per station, so it has to be
polled every 10 minutes; a missed poll is a lost point. Conditional requests
(ETag) make an unchanged poll cost a 304.

Run once per 10 minutes (systemd timer): aranet-weather
"""

import argparse
import json
import logging
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import get_settings

log = logging.getLogger("aranet.weather")

FEED_URL = "https://www.dom.org.cy/AWS/OpenData/CyDoM.xml"
LOCAL_TZ = ZoneInfo("Asia/Nicosia")
KNOT = 0.514444  # m/s

# feed observation name -> (column, multiplier); everything else goes to `extra`
COLUMNS = {
    "Air Temperature (1.2m)": ("temp", 1),
    "Relative Humidity (1.2m)": ("rh", 1),
    "Accumulated Rainfall (10 min.)": ("rain", 1),
    "Wind Speed (2m)": ("wind2", 1),
    "Wind Speed (10m)": ("wind10", KNOT),  # feed gives knots, stored as m/s
    "Wind Direction (10m)": ("wdir", 1),
    "Global Radiation": ("rad_global", 1),
    "Direct Solar Radiation": ("rad_direct", 1),
    "Air Temperature (5cm)": ("temp_5cm", 1),
    "Atmospheric Pressure (Station Level)": ("p_station", 1),
    "Atmospheric Pressure (Mean Sea Level)": ("p_msl", 1),
    "Atmospheric Pressure (QNH)": ("p_qnh", 1),
    "Accumulated Rainfall (24 hours)": ("rain24", 1),
    "Rain Intensity (10 min.)": ("rain_int", 1),
    "Snow Depth": ("snow", 1),
}
# daily extremes since 18 UTC: derivable from the 10-minute temperatures
SKIP = {"Extreme Day Max. Temp.", "Extreme Day Min. Temp."}
VALUE_COLUMNS = list(dict.fromkeys(c for c, _ in COLUMNS.values()))

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS stations (
    code TEXT PRIMARY KEY,
    lat  REAL,
    lon  REAL
);

CREATE TABLE IF NOT EXISTS observations (
    station TEXT NOT NULL,
    ts      INTEGER NOT NULL,
    {", ".join(f"{c} REAL" for c in VALUE_COLUMNS)},
    extra   TEXT,
    PRIMARY KEY (station, ts)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect(path: str) -> sqlite3.Connection:
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def local_to_epoch(text: str, near: float) -> int:
    """'2026-10-03 20:20 (Local Time)' -> unix seconds. In the repeated hour at
    the end of DST the wall time is ambiguous; take the reading closest to `near`."""
    naive = datetime.strptime(text.split("(")[0].strip(), "%Y-%m-%d %H:%M")
    candidates = {int(naive.replace(tzinfo=LOCAL_TZ, fold=f).timestamp()) for f in (0, 1)}
    return min(candidates, key=lambda t: abs(t - near))


def parse(xml_bytes: bytes, now: float | None = None):
    """-> (stations [(code, lat, lon)], observations [dict(station, ts, <columns>, extra)])"""
    now = time.time() if now is None else now
    root = ET.fromstring(xml_bytes)
    stations = []
    for st in root.iter("station"):
        code = (st.findtext("station_code") or "").strip()
        try:
            stations.append((code, float(st.findtext("station_latitude")), float(st.findtext("station_longitude"))))
        except (TypeError, ValueError):
            continue
    rows = []
    for obs in root.iter("observations"):
        code = (obs.findtext("station_code") or "").strip()
        when = obs.findtext("date_time")
        if not code or not when:
            continue
        try:
            ts = local_to_epoch(when, now)
        except ValueError:
            log.warning("bad date_time for %s: %r", code, when)
            continue
        row = {"station": code, "ts": ts, **{c: None for c in VALUE_COLUMNS}}
        extra = {}
        for ob in obs.iter("observation"):
            name = (ob.findtext("observation_name") or "").strip()
            try:
                value = float(ob.findtext("observation_value"))
            except (TypeError, ValueError):
                continue
            if name in SKIP:
                continue
            if name in COLUMNS:
                col, mult = COLUMNS[name]
                row[col] = round(value * mult, 3)
            else:
                extra[f"{name} [{ob.findtext('observation_unit') or ''}]"] = value
        row["extra"] = json.dumps(extra, ensure_ascii=False) if extra else None
        rows.append(row)
    return stations, rows


def store(conn: sqlite3.Connection, stations, rows) -> int:
    cols = ["station", "ts", *VALUE_COLUMNS, "extra"]
    with conn:
        conn.executemany(
            "INSERT INTO stations (code, lat, lon) VALUES (?, ?, ?)"
            " ON CONFLICT(code) DO UPDATE SET lat = excluded.lat, lon = excluded.lon",
            stations,
        )
        changes_after_stations = conn.total_changes
        # a station that hasn't reported since the last poll repeats its old time: ignored
        conn.executemany(
            f"INSERT OR IGNORE INTO observations ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            [tuple(r[c] for c in cols) for r in rows],
        )
    return conn.total_changes - changes_after_stations


def _meta(conn, key):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _set_meta(conn, key, value):
    with conn:
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))


def fetch(url: str, etag: str | None, timeout: int = 30):
    """-> (body or None when unchanged, etag)"""
    req = urllib.request.Request(url, headers={"User-Agent": "aranet-monitor/0.1 (+https://github.com/DarkPatrick/aranet4)"})
    if etag:
        req.add_header("If-None-Match", etag)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(), resp.headers.get("ETag")
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return None, etag
        raise


def collect_once(db_path: str, url: str = FEED_URL) -> int:
    conn = connect(db_path)
    try:
        body, etag = fetch(url, _meta(conn, "etag"))
        if body is None:
            log.info("feed unchanged (304)")
            return 0
        stations, rows = parse(body)
        n = store(conn, stations, rows)
        if etag:
            _set_meta(conn, "etag", etag)
        newest = max((r["ts"] for r in rows), default=None)
        log.info("%d stations, %d new observation(s), newest %s", len(stations), n,
                 datetime.fromtimestamp(newest, LOCAL_TZ).strftime("%Y-%m-%d %H:%M") if newest else "-")
        return n
    finally:
        conn.close()


# ---------- reads for the dashboard ----------

def net(temp, rh, wind):
    """Normal Effective Temperature (Gregorczuk), the "feels like" index the
    Cyprus Department of Meteorology publishes; wind in m/s at 10 m."""
    if temp is None or rh is None or wind is None:
        return None
    v = max(wind, 0.0)
    value = 37 - (37 - temp) / (0.68 - 0.0014 * rh + 1 / (1.76 + 1.4 * v ** 0.75)) - 0.29 * temp * (1 - 0.01 * rh)
    return round(value, 1)


def _wind_for_net(row) -> float | None:
    return row["wind10"] if row["wind10"] is not None else row["wind2"]

def station_list(conn) -> list[dict]:
    """Stations with their latest observation and which columns they ever reported."""
    out = []
    for st in conn.execute("SELECT code, lat, lon FROM stations ORDER BY code"):
        last = conn.execute(
            "SELECT * FROM observations WHERE station = ? ORDER BY ts DESC LIMIT 1", (st["code"],)
        ).fetchone()
        latest = {k: last[k] for k in ("ts", *VALUE_COLUMNS)} if last else None
        if latest:
            latest["net"] = net(last["temp"], last["rh"], _wind_for_net(last))
        has = []
        if last:
            # what the station reports: anything seen in its last week of data
            counts = conn.execute(
                f"SELECT {', '.join(f'COUNT({c}) AS {c}' for c in VALUE_COLUMNS)}"
                " FROM observations WHERE station = ? AND ts > ?",
                (st["code"], last["ts"] - 7 * 86400),
            ).fetchone()
            has = [c for c in VALUE_COLUMNS if counts[c]]
            if counts["temp"] and counts["rh"] and (counts["wind10"] or counts["wind2"]):
                has.append("net")
        first = conn.execute("SELECT MIN(ts) FROM observations WHERE station = ?", (st["code"],)).fetchone()[0]
        out.append({"code": st["code"], "lat": st["lat"], "lon": st["lon"], "first": first, "latest": latest, "metrics": has})
    return out


def readings(conn, station: str, ts_from: int | None = None, ts_to: int | None = None) -> dict:
    sql = f"SELECT ts, {', '.join(VALUE_COLUMNS)} FROM observations WHERE station = ?"
    args: list = [station]
    if ts_from is not None:
        sql += " AND ts >= ?"
        args.append(ts_from)
    if ts_to is not None:
        sql += " AND ts <= ?"
        args.append(ts_to)
    rows = conn.execute(sql + " ORDER BY ts", args).fetchall()
    out = {c: [r[c] for r in rows] for c in ("ts", *VALUE_COLUMNS)}
    out["net"] = [net(r["temp"], r["rh"], _wind_for_net(r)) for r in rows]
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Collect Cyprus weather station data into SQLite")
    parser.add_argument("--config", help="config.env path (default: ./config.env)")
    parser.add_argument("--db", help="SQLite path, overrides ARANET_WEATHER_DB")
    parser.add_argument("--url", default=FEED_URL)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    db_path = args.db or get_settings(args.config).weather_db
    for attempt in range(1, 4):
        try:
            collect_once(db_path, args.url)
            return 0
        except (urllib.error.URLError, TimeoutError, ET.ParseError, OSError) as exc:
            log.warning("attempt %d/3 failed: %s", attempt, exc)
            if attempt < 3:
                time.sleep(20)
    return 1


if __name__ == "__main__":
    sys.exit(main())
