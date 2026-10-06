"""Lightning flashes over Cyprus and the sea around it, from the Lightning Imager on Meteosat-12.

Source: EUMETSAT Data Store, collection "LI Lightning Flashes - MTG - 0 degree"
(EO:EUM:DAT:0691): every flash the satellite saw (time to the millisecond, place,
radiance, duration, footprint), cloud-to-ground and in-cloud alike. The Data Store
publishes it in ten-minute files ~40-50 s after each window closes (the 10-second
stream goes out only over EUMETCast, a satellite broadcast), so the dashboard replays
each window with its real timing, ~11 minutes behind.

Needs a (free) EUMETSAT account: EUMETSAT_KEY / EUMETSAT_SECRET from
https://api.eumetsat.int/api-key/ in config.env. EUMETSAT's servers don't answer
DigitalOcean addresses, so this runs on the Pi and pushes lightning.db to the server
next to aranet.db (ARANET_SYNC_TARGET's directory).

    aranet-lightning        # timer: every 2 minutes
"""

import argparse
import io
import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import get_settings

log = logging.getLogger("aranet.lightning")

COLLECTION = "EO:EUM:DAT:0691"
TOKEN_URL = "https://api.eumetsat.int/token"
SEARCH_URL = "https://api.eumetsat.int/data/search-products/1.0.0/os"
UA = {"User-Agent": "aranet-monitor/0.1 (+https://github.com/DarkPatrick/aranet4)"}
# Cyprus with ~150 km of sea around it (and the Turkish / Levant coasts at the edge)
BOX = (33.3, 36.7, 30.8, 35.9)  # lat min, lat max, lon min, lon max
EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc).timestamp()
KEEP_DAYS = 90
CATCH_UP = timedelta(hours=6)  # on the first run (or after a long outage) look back this far

SCHEMA = """
CREATE TABLE IF NOT EXISTS flashes (
    ts        REAL NOT NULL,     -- unix seconds, ms precision
    lat       REAL NOT NULL,
    lon       REAL NOT NULL,
    radiance  REAL,              -- mW m-2 sr-1
    duration  INTEGER,           -- ms
    footprint INTEGER,           -- flash footprint as the product gives it (no unit in the file)
    PRIMARY KEY (ts, lat, lon)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS lightning_files (
    product  TEXT PRIMARY KEY,
    start    INTEGER,            -- window, unix seconds
    end      INTEGER,
    flashes  INTEGER,            -- in BOX
    fetched  INTEGER
);
"""


def connect(path: str) -> sqlite3.Connection:
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def token(key: str, secret: str) -> str:
    req = urllib.request.Request(TOKEN_URL, data=b"grant_type=client_credentials", headers=UA)
    import base64
    req.add_header("Authorization", "Basic " + base64.b64encode(f"{key}:{secret}".encode()).decode())
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["access_token"]


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def search(since: datetime) -> list[dict]:
    """Products whose window starts at or after `since`, oldest first: [{id, start, end, url}]."""
    params = {"format": "json", "pi": COLLECTION, "dtstart": _iso(since), "dtend": _iso(datetime.now(timezone.utc)),
              "sort": "start,time,1", "c": 100, "si": 0}
    req = urllib.request.Request(SEARCH_URL + "?" + urllib.parse.urlencode(params), headers=UA)
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    out = []
    for f in data.get("features", []):
        start, end = f["properties"]["date"].split("/")
        parse = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))
        links = f["properties"].get("links", {}).get("data", [])
        if links:
            out.append({"id": f["id"], "start": parse(start), "end": parse(end), "url": links[0]["href"]})
    return sorted(out, key=lambda p: p["start"])


def download(url: str, bearer: str, retries: int = 2) -> bytes:
    """The Data Store answers an occasional 5xx: try again a couple of times."""
    req = urllib.request.Request(url, headers={**UA, "Authorization": f"Bearer {bearer}"})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code < 500 or attempt == retries:
                raise
            time.sleep(5 * (attempt + 1))


def parse(blob: bytes, box=BOX) -> list[tuple]:
    """The product zip (or a bare .nc) -> [(ts, lat, lon, radiance, duration, footprint)] inside `box`."""
    import h5py  # only the Pi's collector needs it
    import numpy as np

    if blob[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            # the zip also carries a small trailer .nc: the flashes are in the BODY file
            ncs = [i for i in z.infolist() if i.filename.endswith(".nc")]
            body = [i for i in ncs if "BODY" in i.filename] or sorted(ncs, key=lambda i: -i.file_size)
            blob = z.read(body[0].filename)
    with h5py.File(io.BytesIO(blob), "r") as f:
        def col(name):
            ds = f[name]
            raw = ds[:]
            fill = ds.attrs.get("_FillValue")
            scale = float(np.ravel(ds.attrs.get("scale_factor", [1.0]))[0])
            offset = float(np.ravel(ds.attrs.get("add_offset", [0.0]))[0])
            out = raw.astype("float64") * scale + offset
            if fill is not None:
                out[raw == np.ravel(fill)[0]] = np.nan
            return out
        lat, lon = col("latitude"), col("longitude")
        m = (lat >= box[0]) & (lat <= box[1]) & (lon >= box[2]) & (lon <= box[3])
        if not m.any():
            return []
        t = f["flash_time"][:][m] + EPOCH
        rad, dur, fp = col("radiance")[m], f["flash_duration"][:][m], f["flash_footprint"][:][m]
    return [(round(float(a), 3), round(float(b), 4), round(float(c), 4),
             None if np.isnan(r) else float(r), int(d), int(p))
            for a, b, c, r, d, p in zip(t, lat[m], lon[m], rad, dur, fp)]


def store(conn, product: dict, flashes: list[tuple]) -> None:
    with conn:
        conn.executemany("INSERT OR IGNORE INTO flashes VALUES (?, ?, ?, ?, ?, ?)", flashes)
        conn.execute("INSERT OR REPLACE INTO lightning_files VALUES (?, ?, ?, ?, ?)",
                     (product["id"], int(product["start"].timestamp()), int(product["end"].timestamp()),
                      len(flashes), int(time.time())))
        conn.execute("DELETE FROM flashes WHERE ts < ?", (time.time() - KEEP_DAYS * 86400,))
        conn.execute("DELETE FROM lightning_files WHERE end < ?", (time.time() - KEEP_DAYS * 86400,))


def collect(path: str, key: str, secret: str) -> int:
    """New windows since the last one stored; returns how many files were added."""
    conn = connect(path)
    try:
        last = conn.execute("SELECT MAX(start) FROM lightning_files").fetchone()[0]
        since = (datetime.fromtimestamp(last, timezone.utc) + timedelta(seconds=1) if last
                 else datetime.now(timezone.utc) - CATCH_UP)
        since = max(since, datetime.now(timezone.utc) - CATCH_UP)
        # the search returns every window overlapping `since`, the last one stored included
        known = {r[0] for r in conn.execute("SELECT product FROM lightning_files WHERE end >= ?", (int(since.timestamp()) - 3600,))}
        products = [p for p in search(since) if p["id"] not in known and (last is None or p["start"].timestamp() > last)]
        if not products:
            return 0
        bearer = token(key, secret)
        for p in products:
            flashes = parse(download(p["url"], bearer))
            store(conn, p, flashes)
            log.info("%s..%s: %d flashes near Cyprus", p["start"].strftime("%H:%M"), p["end"].strftime("%H:%M"), len(flashes))
        return len(products)
    finally:
        conn.close()


def recent(conn, ts_from: float, ts_to: float | None = None) -> dict:
    """Flashes in [from, to] (columnar) and the latest window the satellite data covers."""
    ts_to = ts_to if ts_to is not None else time.time() + 60
    try:
        rows = conn.execute("SELECT ts, lat, lon, radiance FROM flashes WHERE ts BETWEEN ? AND ? ORDER BY ts",
                            (ts_from, ts_to)).fetchall()
        last = conn.execute("SELECT start, end, fetched FROM lightning_files ORDER BY end DESC LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        rows, last = [], None
    return {"ts": [r[0] for r in rows], "lat": [r[1] for r in rows], "lon": [r[2] for r in rows],
            "radiance": [r[3] for r in rows], "box": BOX,
            "window": {"start": last[0], "end": last[1], "fetched": last[2]} if last else None}


def push(path: str, target: str, key: str) -> None:
    """lightning.db next to aranet.db on the server (same restricted rsync key)."""
    from . import sync

    snap = str(Path(path).with_name("lightning-snapshot.db"))
    sync.snapshot(path, snap)
    dest = target.rsplit(":", 1)[0] + ":lightning.db" if ":" in target else str(Path(target).with_name("lightning.db"))
    sync.push(snap, dest, key)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Lightning flashes over Cyprus from Meteosat-12 (EUMETSAT Data Store)")
    parser.add_argument("--config", help="config.env path (default: ./config.env)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    s = get_settings(args.config)
    key, secret = os.environ.get("EUMETSAT_KEY", ""), os.environ.get("EUMETSAT_SECRET", "")
    if not key or not secret:
        log.warning("EUMETSAT_KEY / EUMETSAT_SECRET not set: nothing to do")
        return 0
    try:
        n = collect(s.lightning_db, key, secret)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError) as exc:
        log.error("failed: %s", exc)
        return 1
    if n and s.sync_target:
        try:
            push(s.lightning_db, s.sync_target, s.sync_key)
        except (subprocess.CalledProcessError, OSError) as exc:
            log.error("push failed: %s", exc)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
