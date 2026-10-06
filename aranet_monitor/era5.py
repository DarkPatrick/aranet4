"""Temperature records for today's date and hour, since 1950, at the climate-archive stations.

Source: ERA5-Land reanalysis (ECMWF / Copernicus, ~9 km, hourly since 1950) through
Open-Meteo's free historical API, at the station's coordinates (Open-Meteo corrects for
the station's height). A reanalysis smooths the peaks: measured records run ~1-3 degC
beyond it, so the dashboard puts the archive's own (measured, since 2016) records next
to the daily ones.

Only the records are kept, not the 670 000 hours per station: for every calendar date
(MM-DD) and local hour the lowest and highest temperature with when it happened, and
the same for the whole date (hour -1). Each run continues every station from where it
stopped, in ten-year requests, and stays inside the free plan: a request counts as one
call per two weeks of data, 5 000 calls an hour and 10 000 a day, so the first load
spreads over two nights; afterwards a run adds the few days ERA5-Land has published
since (it lags ~5 days).

    aranet-era5            # timer: nightly
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
from datetime import date, datetime, timedelta

from . import weather
from .config import get_settings

log = logging.getLogger("aranet.era5")

API = "https://archive-api.open-meteo.com/v1/archive"
UA = {"User-Agent": "aranet-monitor/0.1 (+https://github.com/DarkPatrick/aranet4)"}
START = date(1950, 1, 1)
CHUNK_YEARS = 10
RUN_BUDGET = 8000   # weighted calls per run (the free plan allows 10 000 a day)
PAUSE = 200         # s between requests: ~260 calls each, 5 000 an hour allowed

# archive stations that aren't live weather stations (closed): their approximate place
EXTRA_COORDS = {"LIMASSOL_PUBLIC_GARDEN": (34.6795, 33.0425), "PARALIMNI_HOSP": (35.0390, 33.9820)}

SCHEMA = """
CREATE TABLE IF NOT EXISTS era5_records (
    station  TEXT NOT NULL,
    md       TEXT NOT NULL,      -- MM-DD
    hour     INTEGER NOT NULL,   -- local hour 0..23, -1 = the whole date
    tmin REAL, tmin_at TEXT,     -- local "YYYY-MM-DDTHH:MM"
    tmax REAL, tmax_at TEXT,
    PRIMARY KEY (station, md, hour)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS era5_progress (
    station TEXT PRIMARY KEY,
    done    TEXT                 -- last local date fully folded in, YYYY-MM-DD
);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = weather.connect(path)
    conn.executescript(SCHEMA)
    return conn


def calls(start: date, end: date) -> float:
    """How Open-Meteo counts a one-variable request: one call per 14 days of data."""
    return max(1.0, ((end - start).days + 1) / 14)


def fetch(lat: float, lon: float, start: date, end: date) -> list[tuple[str, float]]:
    """[(local "YYYY-MM-DDTHH:MM", degC)], hours ERA5-Land doesn't have yet left out."""
    params = {"latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "start_date": start.isoformat(),
              "end_date": end.isoformat(), "hourly": "temperature_2m", "models": "era5_land",
              "timezone": "Asia/Nicosia"}
    req = urllib.request.Request(API + "?" + urllib.parse.urlencode(params), headers=UA)
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    if data.get("error"):
        raise ValueError(data.get("reason"))
    h = data.get("hourly", {})
    return [(t, v) for t, v in zip(h.get("time", []), h.get("temperature_2m", [])) if v is not None]


def fold(conn, station: str, hours: list[tuple[str, float]]) -> None:
    """Merge hourly values into the records (min/max, so folding the same hours twice is harmless)."""
    best: dict[tuple[str, int], list] = {}
    for t, v in hours:
        md, hour = t[5:10], int(t[11:13])
        for key in ((md, hour), (md, -1)):
            b = best.get(key)
            if b is None:
                best[key] = [v, t, v, t]
            else:
                if v < b[0]:
                    b[0], b[1] = v, t
                if v > b[2]:
                    b[2], b[3] = v, t
    old = {(r[0], r[1]): list(r[2:]) for r in conn.execute(
        "SELECT md, hour, tmin, tmin_at, tmax, tmax_at FROM era5_records WHERE station = ?", (station,))}
    rows = []
    for key, (lo, lo_at, hi, hi_at) in best.items():
        o = old.get(key)
        if o:
            if o[0] <= lo:
                lo, lo_at = o[0], o[1]
            if o[2] >= hi:
                hi, hi_at = o[2], o[3]
        rows.append((station, key[0], key[1], lo, lo_at, hi, hi_at))
    with conn:
        conn.executemany("INSERT OR REPLACE INTO era5_records VALUES (?, ?, ?, ?, ?, ?, ?)", rows)


def stations(conn) -> list[tuple[str, float, float]]:
    coords = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT code, lat, lon FROM stations")}
    coords.update({k: v for k, v in EXTRA_COORDS.items() if k not in coords})
    try:
        codes = [r[0] for r in conn.execute("SELECT code FROM climate_stations ORDER BY code")]
    except sqlite3.OperationalError:
        codes = []
    return [(c, *coords[c]) for c in codes if c in coords]


def collect(db_path: str, budget: float = RUN_BUDGET, pause: float = PAUSE) -> int:
    conn = connect(db_path)
    try:
        today, spent, requests = date.today(), 0.0, 0
        for code, lat, lon in stations(conn):
            row = conn.execute("SELECT done FROM era5_progress WHERE station = ?", (code,)).fetchone()
            start = date.fromisoformat(row[0]) + timedelta(days=1) if row else START
            while start < today - timedelta(days=1):
                end = min(date(start.year + CHUNK_YEARS, 1, 1) - timedelta(days=1), today - timedelta(days=1))
                cost = calls(start, end)
                if spent + cost > budget:
                    log.info("budget used (%.0f calls): the rest next run", spent)
                    return requests
                if requests:
                    time.sleep(pause)
                try:
                    hours = fetch(lat, lon, start, end)
                except urllib.error.HTTPError as exc:
                    if exc.code == 429:
                        log.warning("rate limited: stopping until the next run")
                        return requests
                    raise
                spent += cost
                requests += 1
                if not hours:
                    break  # nothing published past `start` yet
                fold(conn, code, hours)
                # the last local date that came complete (ERA5-Land ends mid-day sometimes)
                last = hours[-1][0]
                done = date.fromisoformat(last[:10]) - (timedelta(days=0) if last.endswith("23:00") else timedelta(days=1))
                with conn:
                    conn.execute("INSERT OR REPLACE INTO era5_progress (station, done) VALUES (?, ?)", (code, done.isoformat()))
                log.info("%s: %s .. %s folded in", code, start, done)
                if done < end:
                    break  # the reanalysis hasn't got further yet
                start = done + timedelta(days=1)
        return requests
    finally:
        conn.close()


def records(conn, station: str, when: datetime | None = None) -> dict:
    """Records for the given (default: now) local date and hour, plus the archive's own."""
    when = when or datetime.now(weather.LOCAL_TZ)
    md, hour = when.strftime("%m-%d"), when.hour
    out = {"md": md, "hour": hour, "hourly": None, "daily": None, "period": None, "archive": None}
    try:
        for key, h in (("hourly", hour), ("daily", -1)):
            r = conn.execute("SELECT tmin, tmin_at, tmax, tmax_at FROM era5_records WHERE station = ? AND md = ? AND hour = ?",
                             (station, md, h)).fetchone()
            if r:
                out[key] = {"min": r[0], "min_at": r[1], "max": r[2], "max_at": r[3]}
        p = conn.execute("SELECT done FROM era5_progress WHERE station = ?", (station,)).fetchone()
        if p:
            out["period"] = [START.isoformat(), p[0]]
    except sqlite3.OperationalError:
        pass
    try:  # measured daily extremes in the weather service's archive
        rows = conn.execute("SELECT date, tmax, tmin FROM climate_daily WHERE station = ? AND substr(date, 6, 5) = ?",
                            (station, md)).fetchall()
        hi = max((r for r in rows if r[1] is not None), key=lambda r: r[1], default=None)
        lo = min((r for r in rows if r[2] is not None), key=lambda r: r[2], default=None)
        span = conn.execute("SELECT MIN(date), MAX(date) FROM climate_daily WHERE station = ?", (station,)).fetchone()
        if hi or lo:
            out["archive"] = {"max": hi and hi[1], "max_at": hi and hi[0], "min": lo and lo[2], "min_at": lo and lo[0],
                              "years": len(rows), "period": list(span)}
    except sqlite3.OperationalError:
        pass
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="ERA5-Land temperature records per climate-archive station")
    parser.add_argument("--config", help="config.env path (default: ./config.env)")
    parser.add_argument("--db", help="SQLite path, overrides ARANET_WEATHER_DB")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        n = collect(args.db or get_settings(args.config).weather_db)
        log.info("%d requests", n)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        log.error("failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
