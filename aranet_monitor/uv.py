"""UV index per weather station from CAMS (Copernicus Atmosphere Monitoring Service),
via Open-Meteo's free air-quality API (non-commercial use, no key).

Model data, not measurements: hourly UV index with clouds, ozone and aerosols
(Saharan dust included) and the clear-sky value, on a coarse grid (~0.4 degree,
~40 km: Troodos and Prodromos share one cell), so mountains are smoothed away.
UV rises ~6-10 % per 1000 m of altitude; `uv_alt` adds 8 %/km from sea level using
the station's elevation from Open-Meteo's terrain model (an upper estimate: the
model cell may already account for part of the height). Each run fetches
yesterday..+3 days for every station in one or two requests and overwrites what it
had: past hours settle, forecast hours get refreshed.

The same run stores the hourly global radiation of the weather model (Open-Meteo
forecast API, best-match model) at each station: "feels like in the sun" falls back
to it where no station nearby measures radiation.

    aranet-uv                  # timer: hourly
"""

import argparse
import json
import logging
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from . import weather
from .config import get_settings

log = logging.getLogger("aranet.uv")

API = "https://air-quality-api.open-meteo.com/v1/air-quality"
FORECAST_API = "https://api.open-meteo.com/v1/forecast"
UA = {"User-Agent": "aranet-monitor/0.1 (+https://github.com/DarkPatrick/aranet4)"}
CHUNK = 30  # stations per request, keeps the URL short
ALT_GAIN = 0.08  # UV increase per 1000 m of altitude

SCHEMA = """
CREATE TABLE IF NOT EXISTS uv_station (
    station   TEXT PRIMARY KEY,
    elevation REAL                -- metres, Open-Meteo terrain model at the station
);

CREATE TABLE IF NOT EXISTS rad_model (
    station TEXT NOT NULL,
    ts      INTEGER NOT NULL,   -- END of the hour the mean is over, unix seconds
    ghi     REAL,               -- global horizontal radiation, W/m2, mean of that hour
    PRIMARY KEY (station, ts)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS uv (
    station  TEXT NOT NULL,
    ts       INTEGER NOT NULL,   -- start of the hour, unix seconds
    uv       REAL,
    uv_clear REAL,               -- the same sky without clouds
    fetched  INTEGER,
    PRIMARY KEY (station, ts)
) WITHOUT ROWID;
"""


def connect(path: str) -> sqlite3.Connection:
    conn = weather.connect(path)
    conn.executescript(SCHEMA)
    return conn


def fetch(stations: list[tuple], past_days: int = 1, forecast_days: int = 3) -> list[dict]:
    """[(code, lat, lon)] -> rows; Open-Meteo answers a list in the order asked."""
    params = {
        "latitude": ",".join(f"{lat:.4f}" for _, lat, _ in stations),
        "longitude": ",".join(f"{lon:.4f}" for _, _, lon in stations),
        "hourly": "uv_index,uv_index_clear_sky", "past_days": past_days, "forecast_days": forecast_days,
        "timezone": "GMT", "timeformat": "unixtime",
    }
    req = urllib.request.Request(API + "?" + urllib.parse.urlencode(params), headers=UA)
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    if isinstance(data, dict):
        if data.get("error"):
            raise ValueError(data.get("reason"))
        data = [data]
    now, rows = int(time.time()), []
    for (code, _, _), loc in zip(stations, data):
        if loc.get("elevation") is not None:
            rows.append({"station": code, "elevation": float(loc["elevation"])})
        h = loc.get("hourly", {})
        for ts, u, c in zip(h.get("time", []), h.get("uv_index", []), h.get("uv_index_clear_sky", [])):
            if u is not None or c is not None:
                rows.append({"station": code, "ts": int(ts), "uv": u, "uv_clear": c, "fetched": now})
    return rows


def fetch_radiation(stations: list[tuple], past_days: int = 2, forecast_days: int = 1) -> list[tuple]:
    """[(code, lat, lon)] -> [(code, ts, ghi)]; Open-Meteo stamps a "preceding hour" mean at the hour's end."""
    params = {
        "latitude": ",".join(f"{lat:.4f}" for _, lat, _ in stations),
        "longitude": ",".join(f"{lon:.4f}" for _, _, lon in stations),
        "hourly": "shortwave_radiation", "past_days": past_days, "forecast_days": forecast_days,
        "timezone": "GMT", "timeformat": "unixtime",
    }
    req = urllib.request.Request(FORECAST_API + "?" + urllib.parse.urlencode(params), headers=UA)
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    if isinstance(data, dict):
        if data.get("error"):
            raise ValueError(data.get("reason"))
        data = [data]
    out = []
    for (code, _, _), loc in zip(stations, data):
        h = loc.get("hourly", {})
        out += [(code, int(t), g) for t, g in zip(h.get("time", []), h.get("shortwave_radiation", [])) if g is not None]
    return out


def store(conn, rows: list[dict]) -> int:
    values = [r for r in rows if "ts" in r]
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO uv (station, ts, uv, uv_clear, fetched) VALUES (:station, :ts, :uv, :uv_clear, :fetched)", values)
        conn.executemany("INSERT OR REPLACE INTO uv_station (station, elevation) VALUES (:station, :elevation)",
                         [r for r in rows if "elevation" in r])
    return len(values)


def collect(db_path: str) -> int:
    conn = connect(db_path)
    try:
        stations = [tuple(r) for r in conn.execute("SELECT code, lat, lon FROM stations ORDER BY code")]
        if not stations:
            log.warning("no stations yet: run aranet-weather first")
            return 0
        n = 0
        for i in range(0, len(stations), CHUNK):
            n += store(conn, fetch(stations[i:i + CHUNK]))
        try:  # radiation is a bonus: don't lose the UV run over it
            for i in range(0, len(stations), CHUNK):
                with conn:
                    conn.executemany("INSERT OR REPLACE INTO rad_model (station, ts, ghi) VALUES (?, ?, ?)",
                                     fetch_radiation(stations[i:i + CHUNK]))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            log.warning("model radiation: %s", exc)
        log.info("%d stations, %d hourly UV values stored", len(stations), n)
        return n
    finally:
        conn.close()


def readings(conn, station: str, ts_from: int | None = None, ts_to: int | None = None) -> dict:
    try:
        sql, args = "SELECT ts, uv, uv_clear FROM uv WHERE station = ?", [station]
        if ts_from is not None:
            sql += " AND ts >= ?"
            args.append(ts_from)
        if ts_to is not None:
            sql += " AND ts <= ?"
            args.append(ts_to)
        rows = conn.execute(sql + " ORDER BY ts", args).fetchall()
        elev = conn.execute("SELECT elevation FROM uv_station WHERE station = ?", (station,)).fetchone()
    except sqlite3.OperationalError:  # the collector hasn't created the tables yet
        rows, elev = [], None
    elevation = elev[0] if elev else None
    k = 1 + ALT_GAIN * max(elevation or 0, 0) / 1000
    return {"ts": [r[0] for r in rows], "uv": [r[1] for r in rows], "uv_clear": [r[2] for r in rows],
            "elevation": elevation, "alt_gain": ALT_GAIN,
            "uv_alt": [round(r[1] * k, 2) if r[1] is not None else None for r in rows]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="CAMS UV index per weather station (Open-Meteo)")
    parser.add_argument("--config", help="config.env path (default: ./config.env)")
    parser.add_argument("--db", help="SQLite path, overrides ARANET_WEATHER_DB")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        collect(args.db or get_settings(args.config).weather_db)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        log.error("failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
