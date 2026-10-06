"""Cyprus Department of Meteorology automatic weather stations.

The open-data feed (one XML, all ~55 stations, 10-minute averages, refreshed
every 10 minutes) carries only the latest value per station, so it has to be
polled every 10 minutes; a missed poll is a lost point. Conditional requests
(ETag) make an unchanged poll cost a 304.

Run once per 10 minutes (systemd timer): aranet-weather
"""

import argparse
import bisect
import json
import logging
import math
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import thermal
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


def _wind10(row) -> float | None:
    """UTCI wants wind at 10 m: a 2 m reading scaled by the log profile (z0 = 1 cm), x1.30."""
    if row["wind10"] is not None:
        return row["wind10"]
    return row["wind2"] * 1.30 if row["wind2"] is not None else None


# ---------- solar radiation for "feels like in the sun" ----------
# Global radiation is measured at ~14 stations. A station without a sensor borrows a
# neighbour's within NEAR_KM and NEAR_DH metres of height (clouds over the mountains
# differ from the coast), else the hourly model (Open-Meteo, aranet-uv collects it).
NEAR_KM, NEAR_DH = 15.0, 350.0
NEAR_SMOOTH = 15 * 60  # a neighbour's values averaged over +-15 min: cloud shadows don't line up


def _km(lat1, lon1, lat2, lon2) -> float:
    p = math.pi / 180
    a = math.sin((lat2 - lat1) * p / 2) ** 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(a))


_SITES: dict = {}  # db file -> (when, stations, sensors, elevations): the station list asks 55 times


def _sites(conn):
    key = conn.execute("PRAGMA database_list").fetchone()[2]
    hit = _SITES.get(key)
    if hit and time.time() - hit[0] < 600:
        return hit[1:]
    st = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT code, lat, lon FROM stations")}
    sensors = {r[0] for r in conn.execute(
        "SELECT DISTINCT station FROM observations WHERE ts > ? AND rad_global IS NOT NULL", (int(time.time()) - 7 * 86400,))}
    try:
        elev = dict(conn.execute("SELECT station, elevation FROM uv_station").fetchall())
    except sqlite3.OperationalError:
        elev = {}
    _SITES[key] = (time.time(), st, sensors, elev)
    return st, sensors, elev


def radiation_source(conn, station: str) -> dict:
    """{"kind": "own" | "near" | "model", "station", "km"}"""
    st, sensors, elev = _sites(conn)
    if station in sensors or station not in st:
        return {"kind": "own" if station in sensors else "model", "station": station, "km": 0}
    best = None
    for s in sensors:
        if s not in st:
            continue
        km = _km(*st[station], *st[s])
        dh = abs(elev[station] - elev[s]) if station in elev and s in elev else None
        if km <= NEAR_KM and (dh is None and km <= 10 or dh is not None and dh <= NEAR_DH):
            if best is None or km < best[0]:
                best = (km, s)
    if best:
        return {"kind": "near", "station": best[1], "km": round(best[0], 1)}
    return {"kind": "model", "station": station, "km": 0}


def radiation_series(conn, src: dict, ts: list[int]) -> list[float | None]:
    """Global horizontal radiation, W/m2, at each of `ts` from the given source."""
    if not ts:
        return []
    lo, hi = ts[0] - 3600, ts[-1] + 3600
    if src["kind"] in ("own", "near"):
        rows = conn.execute("SELECT ts, rad_global FROM observations WHERE station = ? AND ts BETWEEN ? AND ? "
                            "AND rad_global IS NOT NULL ORDER BY ts", (src["station"], lo, hi)).fetchall()
        times, vals = [r[0] for r in rows], [r[1] for r in rows]
        if src["kind"] == "own":
            exact = dict(zip(times, vals))
            return [exact.get(t) for t in ts]
        out = []
        for t in ts:  # mean over the window around t
            i, j = bisect.bisect_left(times, t - NEAR_SMOOTH), bisect.bisect_right(times, t + NEAR_SMOOTH)
            out.append(round(sum(vals[i:j]) / (j - i), 1) if j > i else None)
        return out
    try:  # hourly means stamped at the hour's end: interpolate between the hours' middles
        rows = conn.execute("SELECT ts - 1800, ghi FROM rad_model WHERE station = ? AND ts BETWEEN ? AND ? "
                            "AND ghi IS NOT NULL ORDER BY ts", (src["station"], lo, hi + 3600)).fetchall()
    except sqlite3.OperationalError:
        return [None] * len(ts)
    times, vals = [r[0] for r in rows], [r[1] for r in rows]
    out = []
    for t in ts:
        i = bisect.bisect_left(times, t)
        if i < len(times) and times[i] == t:
            out.append(vals[i])
        elif 0 < i < len(times) and times[i] - times[i - 1] <= 2 * 3600:
            f = (t - times[i - 1]) / (times[i] - times[i - 1])
            out.append(round(vals[i - 1] + f * (vals[i] - vals[i - 1]), 1))
        else:
            out.append(None)
    return out


def _feels(conn, code, lat, lon, rows) -> tuple[list, list, dict]:
    """UTCI in the shade and in the sun for observation rows (with ts, temp, rh, wind)."""
    src = radiation_source(conn, code)
    ts = [r["ts"] for r in rows]
    ghi = radiation_series(conn, src, ts)
    if src["kind"] == "own":  # the station's own gaps: fall back to the model for those
        missing = [i for i, g in enumerate(ghi) if g is None]
        if missing:
            model = radiation_series(conn, {"kind": "model", "station": code}, [ts[i] for i in missing])
            for i, g in zip(missing, model):
                ghi[i] = g
    shade, sun = [], []
    for r, g in zip(rows, ghi):
        a, b = thermal.feels(r["temp"], r["rh"], _wind10(r), g, lat, lon, r["ts"])
        shade.append(a)
        sun.append(b)
    return shade, sun, src

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
            shade, sun, src = _feels(conn, st["code"], st["lat"], st["lon"], [last])
            latest.update(utci_shade=shade[0], utci_sun=sun[0], rad_src=src)
            # rain over the last half hour (three 10-min sums): one 10-min value flickers in drizzle
            rain = conn.execute("SELECT SUM(rain) FROM observations WHERE station = ? AND ts > ? AND ts <= ?",
                                (st["code"], last["ts"] - 1800, last["ts"])).fetchone()[0]
            latest["rain_30m"] = round(rain, 1) if rain is not None else None
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
                has += ["net", "utci_shade"]
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
    st = conn.execute("SELECT lat, lon FROM stations WHERE code = ?", (station,)).fetchone()
    if st:
        out["utci_shade"], out["utci_sun"], out["rad_src"] = _feels(conn, station, st["lat"], st["lon"], rows)
    else:
        out["utci_shade"], out["utci_sun"], out["rad_src"] = [None] * len(rows), [None] * len(rows), None
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
