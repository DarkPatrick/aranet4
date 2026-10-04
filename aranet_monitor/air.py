"""Air quality: official measurements plus the CAMS model at every weather station.

Measurements: the national network of the Department of Labour Inspection
(airquality.dli.mlsi.gov.cy, 11 stations: traffic, residential, industrial and one
rural background site). Hourly values in ug/m3 come from the JSON behind the site's
graphs (`/station_data/<id>/<from>/<to>`, local time); the history goes back to 2016.

Model: CAMS European air-quality forecast (Copernicus) via Open-Meteo's free
air-quality API (non-commercial use, no key), ~0.1 degree (~10 km) over Cyprus,
yesterday..+4 days at the coordinates of each weather station, including Saharan
dust and the European AQI.

The dashboard shows, for the selected weather station, the model at its coordinates
and, per pollutant, the nearest network station that measures it.

    aranet-air                     # timer: hourly
    aranet-air --since 2016-01-01  # backfill the measurements (resumable, 10 s per request)
"""

import argparse
import json
import logging
import math
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from . import weather
from .config import get_settings

log = logging.getLogger("aranet.air")

DLI = "https://www.airquality.dli.mlsi.gov.cy/station_data/{id}/{start}/{end}"
API = "https://air-quality-api.open-meteo.com/v1/air-quality"
UA = {"User-Agent": "aranet-monitor/0.1 (+https://github.com/DarkPatrick/aranet4)"}
CHUNK = 30  # weather stations per Open-Meteo request
DLI_CHUNK_DAYS = 60  # one station_data request covers at most this many days
CRAWL_DELAY = 10  # s between requests to the DLI site, as its robots.txt asks

# (DLI id, code, name, kind, lat, lon). Coordinates from the site's JSON:API
# (/jsonapi/node/station); the two EAC stations have none there, so those are the
# villages' (approximate).
STATIONS = [
    (1, "NICTRA", "Nicosia", "транспортная", 35.1519, 33.3478),
    (2, "NICRES", "Nicosia", "жилой район", 35.1269, 33.3317),
    (3, "LIMTRA", "Limassol", "транспортная", 34.6861, 33.0356),
    (5, "LARTRA", "Larnaca", "транспортная", 34.9167, 33.6275),
    (7, "PAFTRA", "Paphos", "транспортная", 34.7728, 32.4181),
    (8, "ZYGIND", "Zygi", "промышленная", 34.7294, 33.3375),
    (9, "MARIND", "Mari", "промышленная", 34.7392, 33.2989),
    (10, "AYMBGR", "Ayia Marina Xyliatou", "фоновая", 35.0381, 33.0578),
    (11, "PARTRA", "Paralimni", "транспортная", 35.0458, 33.9778),
    (12, "KALIND", "Kalavasos", "промышленная", 34.7695, 33.3022),
    (13, "ORMIND", "Ormidia", "промышленная", 34.9918, 33.7780),
]
BY_CODE = {s[1]: s for s in STATIONS}

# our column -> DLI pollutant field / Open-Meteo variable
DLI_FIELDS = {"pm25": "pollutant_6001", "pm10": "pollutant_5", "no2": "pollutant_8", "o3": "pollutant_7",
              "so2": "pollutant_1", "co": "pollutant_10"}
CAMS_VARS = {"pm25": "pm2_5", "pm10": "pm10", "no2": "nitrogen_dioxide", "o3": "ozone",
             "so2": "sulphur_dioxide", "dust": "dust", "eaqi": "european_aqi"}
POLLUTANTS = ["pm25", "pm10", "no2", "o3", "so2"]  # measured and modelled

SCHEMA = """
CREATE TABLE IF NOT EXISTS air_obs (
    station TEXT NOT NULL,      -- DLI code (NICTRA, ...)
    ts      INTEGER NOT NULL,   -- unix seconds
    pm25 REAL, pm10 REAL, no2 REAL, o3 REAL, so2 REAL, co REAL,   -- ug/m3
    PRIMARY KEY (station, ts)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS air_model (
    station TEXT NOT NULL,      -- weather station code
    ts      INTEGER NOT NULL,   -- start of the hour, unix seconds
    pm25 REAL, pm10 REAL, no2 REAL, o3 REAL, so2 REAL, dust REAL,  -- ug/m3
    eaqi REAL,                  -- European AQI
    fetched INTEGER,
    PRIMARY KEY (station, ts)
) WITHOUT ROWID;
"""


def connect(path: str) -> sqlite3.Connection:
    conn = weather.connect(path)
    conn.executescript(SCHEMA)
    return conn


def distance_km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))


# ---------- measurements (DLI) ----------

def _get_json(url: str):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def parse_dli(code: str, data: dict) -> list[dict]:
    rows = []
    for item in (data.get("data") or {}).values():
        try:
            naive = datetime.strptime(item["date_time"], "%Y-%m-%d %H:%M:%S")
        except (KeyError, ValueError):
            continue
        row = {"station": code, "ts": int(naive.replace(tzinfo=weather.LOCAL_TZ).timestamp())}
        for col, field in DLI_FIELDS.items():
            v = item.get(field)
            try:
                row[col] = round(float(v), 2) if v not in (None, "") else None
            except ValueError:
                row[col] = None
        if any(row[c] is not None for c in DLI_FIELDS):
            rows.append(row)
    return rows


def fetch_dli(station_id: int, code: str, start: datetime, end: datetime) -> list[dict]:
    fmt = lambda d: urllib.parse.quote(d.strftime("%Y-%m-%d %H:%M"), safe="")
    return parse_dli(code, _get_json(DLI.format(id=station_id, start=fmt(start), end=fmt(end))))


def store_obs(conn, rows: list[dict]) -> int:
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO air_obs (station, ts, pm25, pm10, no2, o3, so2, co) "
            "VALUES (:station, :ts, :pm25, :pm10, :no2, :o3, :so2, :co)", rows)
    return len(rows)


class Blocked(Exception):
    """The site refused us (403/429): stop instead of hammering it."""


def _fetch_politely(sid, code, start, end, retries=(60, 300)) -> list[dict] | None:
    """fetch_dli with backoff; None after the last retry fails (the caller skips that chunk)."""
    for attempt in range(len(retries) + 1):
        try:
            return fetch_dli(sid, code, start, end)
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 429):
                raise Blocked(f"{code}: HTTP {exc.code}") from exc
            err = exc
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            err = exc
        if attempt < len(retries):
            log.warning("%s %s..%s: %s, retrying in %d s", code, start, end, err, retries[attempt])
            time.sleep(retries[attempt])
    log.warning("%s %s..%s: giving up on this chunk (%s)", code, start, end, err)
    return None


def backfill_obs(conn, since: datetime, pause: float = CRAWL_DELAY, chunk_days: int = 90) -> int:
    """History back to `since`, newest chunk first, from each station's earliest stored value
    (so an interrupted run resumes). One request every `pause` s (the site's robots.txt asks
    for Crawl-delay: 10). A station with a whole year of nothing hadn't started yet."""
    n = 0
    for sid, code, *_ in STATIONS:
        first = conn.execute("SELECT MIN(ts) FROM air_obs WHERE station = ?", (code,)).fetchone()[0]
        end = (datetime.fromtimestamp(first, weather.LOCAL_TZ).replace(tzinfo=None) if first
               else datetime.now(weather.LOCAL_TZ).replace(tzinfo=None) + timedelta(hours=1))
        empty, got = 0, 0
        while end > since and empty * chunk_days < 365:
            start = max(since, end - timedelta(days=chunk_days))
            rows = _fetch_politely(sid, code, start, end)
            if rows is not None:
                got += store_obs(conn, rows)
                empty = 0 if rows else empty + 1
            end = start
            time.sleep(pause)
        n += got
        log.info("%s: %d hours, back to %s", code, got, end.date() if empty * chunk_days < 365 else f"{end.date()} (nothing earlier)")
    return n


def collect_obs(conn, default_days: int = 30, pause: float = CRAWL_DELAY) -> int:
    """Per station: from a few hours before its last value (values get revised), else
    `default_days` back; in chunks, up to the next hour, `pause` s between requests."""
    now = datetime.now(weather.LOCAL_TZ).replace(tzinfo=None)
    end, n = now + timedelta(hours=1), 0
    for sid, code, *_ in STATIONS:
        last = conn.execute("SELECT MAX(ts) FROM air_obs WHERE station = ?", (code,)).fetchone()[0]
        start = (datetime.fromtimestamp(last, weather.LOCAL_TZ).replace(tzinfo=None) - timedelta(hours=6)
                 if last else now - timedelta(days=default_days))
        while start < end:
            stop = min(start + timedelta(days=DLI_CHUNK_DAYS), end)
            try:
                n += store_obs(conn, fetch_dli(sid, code, start, stop))
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                log.warning("%s %s..%s: %s", code, start, stop, exc)  # some stations are offline: go on
                break
            finally:
                time.sleep(pause)
            start = stop
    return n


# ---------- model (CAMS via Open-Meteo) ----------

def fetch_model(stations: list[tuple], past_days: int = 1, forecast_days: int = 4) -> list[dict]:
    """[(code, lat, lon)] -> rows; Open-Meteo answers a list in the order asked."""
    params = {
        "latitude": ",".join(f"{lat:.4f}" for _, lat, _ in stations),
        "longitude": ",".join(f"{lon:.4f}" for _, _, lon in stations),
        "hourly": ",".join(CAMS_VARS.values()), "past_days": past_days, "forecast_days": forecast_days,
        "timezone": "GMT", "timeformat": "unixtime",
    }
    data = _get_json(API + "?" + urllib.parse.urlencode(params))
    if isinstance(data, dict):
        if data.get("error"):
            raise ValueError(data.get("reason"))
        data = [data]
    now, rows = int(time.time()), []
    for (code, _, _), loc in zip(stations, data):
        h = loc.get("hourly", {})
        for i, ts in enumerate(h.get("time", [])):
            row = {"station": code, "ts": int(ts), "fetched": now}
            for col, var in CAMS_VARS.items():
                vals = h.get(var) or []
                row[col] = vals[i] if i < len(vals) else None
            if any(row[c] is not None for c in CAMS_VARS):
                rows.append(row)
    return rows


def store_model(conn, rows: list[dict]) -> int:
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO air_model (station, ts, pm25, pm10, no2, o3, so2, dust, eaqi, fetched) "
            "VALUES (:station, :ts, :pm25, :pm10, :no2, :o3, :so2, :dust, :eaqi, :fetched)", rows)
    return len(rows)


def collect_model(conn) -> int:
    stations = [tuple(r) for r in conn.execute("SELECT code, lat, lon FROM stations ORDER BY code")]
    if not stations:
        log.warning("no weather stations yet: run aranet-weather first")
        return 0
    # the first run also fills the past month, so the model can be compared with the measurements
    past = 1 if conn.execute("SELECT 1 FROM air_model LIMIT 1").fetchone() else 30
    return sum(store_model(conn, fetch_model(stations[i:i + CHUNK], past_days=past)) for i in range(0, len(stations), CHUNK))


def collect(db_path: str) -> tuple[int, int]:
    conn = connect(db_path)
    try:
        failed = None
        try:
            model = collect_model(conn)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            model, failed = 0, exc
            log.error("CAMS: %s", exc)
        obs = collect_obs(conn)
        log.info("%d hourly measurements, %d model hours stored", obs, model)
        if failed and not obs:
            raise failed
        return obs, model
    finally:
        conn.close()


# ---------- reads for the dashboard ----------

def readings(conn, station: str, ts_from: int | None = None, ts_to: int | None = None) -> dict:
    """One hourly axis for the weather station: <p>_cams from the model at its coordinates,
    <p> from the nearest network station measuring <p> in this period; `sources` says which."""
    lo, hi = ts_from if ts_from is not None else 0, ts_to if ts_to is not None else 2 ** 40
    try:
        st = conn.execute("SELECT lat, lon FROM stations WHERE code = ?", (station,)).fetchone()
        model = conn.execute("SELECT ts, pm25, pm10, no2, o3, so2, dust, eaqi FROM air_model "
                             "WHERE station = ? AND ts BETWEEN ? AND ? ORDER BY ts", (station, lo, hi)).fetchall()
        sources, obs = {}, {}
        if st:
            for p in POLLUTANTS:
                have = {r[0] for r in conn.execute(
                    f"SELECT DISTINCT station FROM air_obs WHERE ts BETWEEN ? AND ? AND {p} IS NOT NULL", (lo, hi))}
                near = sorted((distance_km(st[0], st[1], s[4], s[5]), s) for s in STATIONS if s[1] in have)
                if not near:
                    continue
                d, (_, code, name, kind, lat, lon) = near[0]
                sources[p] = {"code": code, "name": name, "kind": kind, "lat": lat, "lon": lon, "km": round(d, 1)}
                obs[p] = dict(conn.execute(f"SELECT ts, {p} FROM air_obs WHERE station = ? AND ts BETWEEN ? AND ?",
                                           (code, lo, hi)).fetchall())
    except sqlite3.OperationalError:  # the collector hasn't created the tables yet
        model, sources, obs = [], {}, {}
    cols = ["pm25", "pm10", "no2", "o3", "so2", "dust", "eaqi"]
    by_ts = {r[0]: r for r in model}
    ts = sorted(set(by_ts) | {t for o in obs.values() for t in o})
    out = {"ts": ts, "sources": sources}
    for i, c in enumerate(cols, 1):
        out[c + "_cams"] = [by_ts[t][i] if t in by_ts else None for t in ts]
    for p in POLLUTANTS:
        out[p] = [obs[p].get(t) for t in ts] if p in obs else []
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Air quality: DLI measurements + CAMS model per weather station")
    parser.add_argument("--config", help="config.env path (default: ./config.env)")
    parser.add_argument("--db", help="SQLite path, overrides ARANET_WEATHER_DB")
    parser.add_argument("--since", help="only backfill the measurements back to this date (YYYY-MM-DD; "
                        "the history starts in 2016); resumable, ~10 s per request")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    path = args.db or get_settings(args.config).weather_db
    try:
        if args.since:
            conn = connect(path)
            try:
                log.info("backfill done: %d hours", backfill_obs(conn, datetime.strptime(args.since, "%Y-%m-%d")))
            finally:
                conn.close()
        else:
            collect(path)
    except Blocked as exc:
        log.error("the site refused us, stopping: %s", exc)
        return 1
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        log.error("failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
