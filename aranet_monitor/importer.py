"""Import readings exported from the Aranet Home app (CSV or XLSX).

The app exports "Time(DD/MM/YYYY h:mm:ss A), Carbon dioxide(ppm), Temperature(°C),
Relative humidity(%), Atmospheric pressure(mmHg)" in local time; units follow the
app settings, so they are read from the header (°C/°F, hPa/mmHg). Points that are
already in the database (anything within `tolerance` seconds, e.g. collected from
the device memory, which has finer pressure) are kept and the file's copy skipped.

    aranet-import export.csv export.xlsx [--dry-run]
"""

import argparse
import bisect
import csv
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

from . import db, weather
from .config import get_settings

log = logging.getLogger("aranet.import")
MMHG_TO_HPA = 1 / 0.750062
TIME_FORMATS = ["%d/%m/%Y %I:%M:%S %p", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S"]


def read_table(path: str) -> list[list]:
    if path.lower().endswith((".xlsx", ".xlsm")):
        import openpyxl  # optional: pip install openpyxl

        wb = openpyxl.load_workbook(path, read_only=True)
        return [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [row for row in csv.reader(f)]


def _column(header: list[str], *words: str) -> int | None:
    for i, h in enumerate(header):
        if h and all(w in str(h).lower() for w in words):
            return i
    return None


def _local_ts(text: str) -> int:
    for fmt in TIME_FORMATS:
        try:
            naive = datetime.strptime(str(text).strip(), fmt)
            break
        except ValueError:
            continue
    else:
        raise ValueError(f"unknown time format: {text!r}")
    return int(naive.replace(tzinfo=weather.LOCAL_TZ).timestamp())


def _num(v):
    if v is None or str(v).strip() in ("", "-"):
        return None
    return float(str(v).replace(",", "."))


def parse(path: str) -> list[db.Reading]:
    rows = read_table(path)
    header = [str(h or "") for h in rows[0]]
    c_time, c_co2 = _column(header, "time"), _column(header, "carbon")
    c_temp, c_rh, c_p = _column(header, "temperature"), _column(header, "humidity"), _column(header, "pressure")
    if c_time is None:
        raise ValueError(f"{path}: no time column in {header}")
    fahrenheit = c_temp is not None and "°f" in header[c_temp].lower()
    mmhg = c_p is not None and "mmhg" in header[c_p].lower()
    out = []
    for r in rows[1:]:
        if not r or r[c_time] in (None, ""):
            continue
        get = lambda c: _num(r[c]) if c is not None and c < len(r) else None
        temp, p = get(c_temp), get(c_p)
        if temp is not None and fahrenheit:
            temp = round((temp - 32) * 5 / 9, 1)
        if p is not None and mmhg:
            p = round(p * MMHG_TO_HPA, 1)
        co2 = get(c_co2)
        out.append(db.Reading(ts=_local_ts(r[c_time]), co2=int(co2) if co2 is not None else None,
                              temperature=temp, humidity=get(c_rh), pressure=p))
    return out


def merge(conn, readings: list[db.Reading], tolerance: int = 150, dry_run: bool = False) -> dict:
    existing = sorted(r[0] for r in conn.execute("SELECT ts FROM readings"))
    new, dup = [], 0
    for r in sorted(readings, key=lambda r: r.ts):
        i = bisect.bisect_left(existing, r.ts - tolerance)
        if i < len(existing) and existing[i] <= r.ts + tolerance:
            dup += 1
            continue
        new.append(r)
        bisect.insort(existing, r.ts)
    if not dry_run and new:
        with conn:
            conn.executemany(
                "INSERT OR IGNORE INTO readings (ts, co2, temperature, humidity, pressure) VALUES (?, ?, ?, ?, ?)",
                [(r.ts, r.co2, r.temperature, r.humidity, r.pressure) for r in new])
    return {"read": len(readings), "new": len(new), "already_there": dup,
            "from": min((r.ts for r in new), default=None), "to": max((r.ts for r in new), default=None)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Import Aranet Home app exports (CSV / XLSX)")
    parser.add_argument("files", nargs="+")
    parser.add_argument("--config", help="config.env path (default: ./config.env)")
    parser.add_argument("--db", help="SQLite path, overrides ARANET_DB")
    parser.add_argument("--dry-run", action="store_true", help="only report what would be imported")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    conn = db.connect(args.db or get_settings(args.config).db_path)
    fmt = lambda ts: datetime.fromtimestamp(ts, weather.LOCAL_TZ).strftime("%Y-%m-%d %H:%M") if ts else "-"
    for f in args.files:
        res = merge(conn, parse(f), dry_run=args.dry_run)
        log.info("%s: %d rows, %d new (%s .. %s), %d already in the database%s", Path(f).name, res["read"], res["new"],
                 fmt(res["from"]), fmt(res["to"]), res["already_there"], " [dry run]" if args.dry_run else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
