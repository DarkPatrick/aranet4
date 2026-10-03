"""More from the Cyprus Department of Meteorology (dom.org.cy):

* sea forecasts A-D (text, 4 issues a day): sea surface temperature, pressure,
  synopsis, visibility, warnings, wind / sea state per coast;
* issued weather warnings page (empty when there are none);
* daily climatological archive since 2016: monthly PDFs with Tmax / Tmin /
  precipitation for 5-7 main stations (laid out as text with pdfplumber).

    aranet-dom forecast        # sea forecasts + warnings (timer: every 30 min)
    aranet-dom climate         # new / recent monthly archive PDFs (timer: daily)
"""

import argparse
import html
import json
import logging
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from . import weather
from .config import get_settings

log = logging.getLogger("aranet.dom")

BASE = "https://www.dom.org.cy"
SEA_PAGES = {k: f"{BASE}/FORECAST/sea_{k.lower()}_en.html" for k in "ABCD"}
# the Department's current site; the old {BASE}/WARNING/ page is no longer filled in
WARNINGS_URL = "https://agromet.dom.org.cy/weather/warnings"
# EUMETNET Meteoalarm: the same warnings as structured CAP (English + Greek)
METEOALARM_URL = "https://feeds.meteoalarm.org/api/v1/warnings/feeds-cyprus"
RADAR_IMAGES = ["RADAR_Static.png", "RADAR_PFO_MAX_Static.png", "RADAR_LCA_MAX_Static.png"]
RADAR_URL = f"{BASE}/RADAR_IMG/"
CLIMATE_ROOT = f"{BASE}/CLIMATOLOGY/English/Daily%20Temperature%20and%20Precipitation%20Data/"
UA = {"User-Agent": "aranet-monitor/0.1 (+https://github.com/DarkPatrick/aranet4)"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS sea_forecasts (
    issue      TEXT NOT NULL,       -- A / B / C / D
    valid_from INTEGER NOT NULL,
    valid_to   INTEGER,
    issued     INTEGER,
    pressure   REAL,
    sst        REAL,
    visibility TEXT,
    warnings   TEXT,
    synopsis   TEXT,
    areas      TEXT,                -- JSON: {coast: [[period, wind, sea state], ...]}
    fetched    INTEGER,
    PRIMARY KEY (issue, valid_from)
);

CREATE TABLE IF NOT EXISTS warnings (
    ts   INTEGER PRIMARY KEY,       -- when this text was first seen
    text TEXT NOT NULL              -- '' = no warnings in force
);

CREATE TABLE IF NOT EXISTS alerts (
    identifier TEXT PRIMARY KEY,    -- CAP identifier
    sent       INTEGER,
    msg_type   TEXT,                -- Alert / Update / Cancel
    refs       TEXT,                -- identifiers this one updates or cancels
    level      INTEGER,             -- 2 yellow, 3 orange, 4 red
    type       INTEGER,             -- Meteoalarm awareness type (3 = thunderstorm, ...)
    event      TEXT,
    onset      INTEGER,
    expires    INTEGER,
    headline   TEXT,
    description TEXT,
    instruction TEXT,
    description_el TEXT,
    areas      TEXT
);

CREATE TABLE IF NOT EXISTS radar (
    image   TEXT PRIMARY KEY,
    updated INTEGER,                -- Last-Modified of the image on dom.org.cy
    checked INTEGER
);

CREATE TABLE IF NOT EXISTS climate_stations (
    code TEXT PRIMARY KEY,
    name TEXT
);

CREATE TABLE IF NOT EXISTS climate_daily (
    station TEXT NOT NULL,
    date    TEXT NOT NULL,          -- YYYY-MM-DD, local
    tmax    REAL,
    tmin    REAL,
    rain    REAL,                   -- mm; traces count as 0 with trace = 1
    trace   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (station, date)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS climate_files (
    url     TEXT PRIMARY KEY,
    year    INTEGER,
    month   INTEGER,
    fetched INTEGER,
    rows    INTEGER
);
"""

# archive station name -> AWS station code, so the archive lines up with live data
CLIMATE_CODES = {
    "pafos airport": "LCPH",
    "larnaka airport": "LCLK",
    "athalassa": "ATHALASSA",
    "new limassol port": "LIMASSOL",
    "paralimni (st. frenaros)": "FRENAROS",
    "prodromos (cfc)": "PRODROMOS",
}


def connect(path: str) -> sqlite3.Connection:
    conn = weather.connect(path)
    conn.executescript(SCHEMA)
    return conn


def get(url: str, timeout: int = 30) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as resp:
        return resp.read()


def html_text(raw: bytes) -> list[str]:
    """Visible text of a page, one block per line."""
    s = raw.decode("utf-8", errors="replace")
    s = re.sub(r"<(style|script)[^>]*>.*?</\1>", "", s, flags=re.S | re.I)
    s = re.sub(r"<br\s*/?>|</tr>|</td>|</th>|</p>|</div>|</h\d>|</li>", "\n", s, flags=re.I)
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    return [re.sub(r"\s+", " ", line).strip() for line in s.splitlines() if line.strip()]


def _local_ts(date_ddmmyyyy: str, hhmm: str) -> int:
    d = datetime.strptime(date_ddmmyyyy, "%d/%m/%Y")
    h, m = int(hhmm[:2]), int(hhmm[2:])
    extra = 0
    if h == 24:  # "UNTIL 2400"
        h, extra = 0, 86400
    return int(d.replace(hour=h, minute=m, tzinfo=weather.LOCAL_TZ).timestamp()) + extra


# ---------- sea forecasts ----------

def parse_sea(raw: bytes, issue: str) -> dict | None:
    lines = html_text(raw)
    text = "\n".join(lines)
    period = re.search(r"FROM\s+(\d{4})\s+(\d{2}/\d{2}/\d{4})\s+UNTIL\s+(\d{4})\s+(\d{2}/\d{2}/\d{4})", text)
    if not period:
        return None
    num = lambda pat: (lambda m: float(m.group(1)) if m else None)(re.search(pat, text))
    field = lambda name: (lambda m: m.group(1).strip() if m else None)(re.search(rf"^{name}:\s*(.*)$", text, re.M))

    # synopsis: the paragraph(s) between the pressure line and "Visibility:"
    synopsis = []
    try:
        start = next(i for i, l in enumerate(lines) if l.startswith("Atmospheric pressure")) + 1
        for l in lines[start:]:
            if l.startswith(("Visibility:", "Sea surface temperature:", "Warnings:")):
                break
            synopsis.append(l)
    except StopIteration:
        pass

    # coast table: AREA / PERIOD / WIND / STATE OF SEA, then coast, (period, wind, state) x 3 ...
    areas: dict[str, list] = {}
    if "STATE OF SEA" in lines:
        body = lines[lines.index("STATE OF SEA") + 1:]
        coast = None
        i = 0
        while i < len(body):
            l = body[i]
            if l.startswith(("Time of issue", "Date:", "Note:")):
                break
            if l.endswith("Coast"):
                coast = l
                areas[coast] = []
                i += 1
                continue
            if coast and l in ("Morning", "Afternoon", "Night", "Evening") and i + 2 < len(body):
                areas[coast].append([l, body[i + 1], body[i + 2]])
                i += 3
                continue
            i += 1

    issued = None
    t, d = re.search(r"Time of issue:\s*(\d{4})", text), re.search(r"^Date:\s*(\d{2}/\d{2}/\d{4})", text, re.M)
    if t and d:
        issued = _local_ts(d.group(1), t.group(1))

    return {
        "issue": issue,
        "valid_from": _local_ts(period.group(2), period.group(1)),
        "valid_to": _local_ts(period.group(4), period.group(3)),
        "issued": issued,
        "pressure": num(r"pressure at the time of issue:\s*([\d.]+)"),
        "sst": num(r"Sea surface temperature:\s*([\d.]+)"),
        "visibility": field("Visibility"),
        "warnings": field("Warnings"),
        "synopsis": " ".join(synopsis) or None,
        "areas": json.dumps(areas, ensure_ascii=False) if areas else None,
    }


def store_sea(conn, fc: dict) -> int:
    cols = ["issue", "valid_from", "valid_to", "issued", "pressure", "sst", "visibility", "warnings", "synopsis", "areas", "fetched"]
    row = {**fc, "fetched": int(time.time())}
    before = conn.total_changes
    with conn:
        # an issue can be re-published with corrections: keep the latest text
        conn.execute(
            f"INSERT OR REPLACE INTO sea_forecasts ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            [row[c] for c in cols],
        )
    return conn.total_changes - before


# ---------- warnings ----------

NO_WARNINGS = "Καμία Προειδοποίηση"  # "no warning"


def parse_warnings(raw: bytes) -> str:
    """Text of the warning card on agromet.dom.org.cy; '' when there is none."""
    s = raw.decode("utf-8", errors="replace")
    m = re.search(r'id="warning_card"[^>]*>(.*?)<div class="card-footer', s, re.S)
    if not m:  # page layout changed: better to show nothing than the whole page
        log.warning("warnings page: card not found")
        return ""
    text = "\n".join(html_text(m.group(1).encode())).strip()
    return "" if text == NO_WARNINGS else text


def _iso(ts: str | None) -> int | None:
    return int(datetime.fromisoformat(ts).timestamp()) if ts else None


def parse_meteoalarm(raw: bytes) -> list[dict]:
    out = []
    for w in json.loads(raw).get("warnings", []):
        a = w.get("alert", {})
        infos = a.get("info", [])
        en = next((i for i in infos if str(i.get("language", "")).startswith("en")), infos[0] if infos else {})
        el = next((i for i in infos if str(i.get("language", "")).startswith("el")), {})
        params = {p.get("valueName"): p.get("value", "") for p in en.get("parameter", [])}
        num = lambda v: int(v.split(";")[0]) if v and v.split(";")[0].strip().isdigit() else None
        refs = " ".join(r.split(",")[1] for r in (a.get("references") or "").split() if r.count(",") == 2)
        out.append({
            "identifier": a.get("identifier"), "sent": _iso(a.get("sent")), "msg_type": a.get("msgType"),
            "refs": refs or None, "level": num(params.get("awareness_level")), "type": num(params.get("awareness_type")),
            "event": en.get("event"), "onset": _iso(en.get("onset") or en.get("effective")), "expires": _iso(en.get("expires")),
            "headline": en.get("headline"), "description": en.get("description"), "instruction": en.get("instruction"),
            "description_el": el.get("description"),
            "areas": ", ".join(x.get("areaDesc", "") for x in en.get("area", [])) or None,
        })
    return [r for r in out if r["identifier"]]


def store_alerts(conn, alerts: list[dict]) -> int:
    cols = ["identifier", "sent", "msg_type", "refs", "level", "type", "event", "onset", "expires",
            "headline", "description", "instruction", "description_el", "areas"]
    before = conn.total_changes
    with conn:
        conn.executemany(
            f"INSERT OR REPLACE INTO alerts ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            [[a[c] for c in cols] for a in alerts])
    return conn.total_changes - before


def active_alerts(conn, now: int | None = None) -> list[dict]:
    """Alerts in force or still to come: not expired, not cancelled, not superseded
    by an update (CAP `references`)."""
    now = now or int(time.time())
    rows = [dict(r) for r in conn.execute("SELECT * FROM alerts ORDER BY sent")]
    replaced = {ref for r in rows if r["refs"] for ref in r["refs"].split()}
    return [r for r in rows
            if r["msg_type"] != "Cancel" and r["identifier"] not in replaced and (r["expires"] or 0) > now]


def store_warnings(conn, text: str, now: int | None = None) -> bool:
    last = conn.execute("SELECT text FROM warnings ORDER BY ts DESC LIMIT 1").fetchone()
    if last is not None and last["text"] == text:
        return False
    with conn:
        conn.execute("INSERT OR REPLACE INTO warnings (ts, text) VALUES (?, ?)", (now or int(time.time()), text))
    return True


def check_radar(conn) -> None:
    """The radar images are replaced in place; record when each last changed so the
    dashboard can hide a radar that stopped updating (it did, summer 2026)."""
    from email.utils import parsedate_to_datetime

    for image in RADAR_IMAGES:
        req = urllib.request.Request(RADAR_URL + image, headers=UA, method="HEAD")
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                lm = resp.headers.get("Last-Modified")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            log.warning("radar %s: %s", image, exc)
            continue
        updated = int(parsedate_to_datetime(lm).timestamp()) if lm else None
        with conn:
            conn.execute("INSERT OR REPLACE INTO radar (image, updated, checked) VALUES (?, ?, ?)",
                         (image, updated, int(time.time())))


def collect_forecast(db_path: str) -> None:
    conn = connect(db_path)
    try:
        for issue, url in SEA_PAGES.items():
            try:
                fc = parse_sea(get(url), issue)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                log.warning("sea forecast %s: %s", issue, exc)
                continue
            if fc:
                store_sea(conn, fc)
                log.info("sea %s: valid from %s, SST %s, warnings %s", issue,
                         datetime.fromtimestamp(fc["valid_from"], weather.LOCAL_TZ).strftime("%d.%m %H:%M"), fc["sst"], fc["warnings"])
            else:
                log.warning("sea forecast %s: page not recognised", issue)
        check_radar(conn)
        try:
            alerts = parse_meteoalarm(get(METEOALARM_URL))
            store_alerts(conn, alerts)
            log.info("meteoalarm: %d in feed, %d active", len(alerts), len(active_alerts(conn)))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            log.warning("meteoalarm: %s", exc)
        text = parse_warnings(get(WARNINGS_URL))
        changed = store_warnings(conn, text)
        log.info("warnings: %s%s", (text[:80] or "none"), " (changed)" if changed else "")
    finally:
        conn.close()


# ---------- climatological archive ----------

def _links(url: str) -> list[str]:
    raw = get(url).decode("utf-8", errors="replace")
    hrefs = re.findall(r"""href\s*=\s*['"]?([^'" >]+(?:[^'">]*[^'" >])?)""", raw)
    out = []
    for h in hrefs:
        if h.startswith(("http", "/", "..", "#")) or h.endswith((".css", ".ico", ".png")) or "index" in h.lower():
            continue
        out.append(urllib.parse.urljoin(url, urllib.parse.quote(h.rstrip("/"), safe="/%") + ("" if h.lower().endswith(".pdf") else "/")))
    return out


def archive_pdfs(min_year: int | None = None) -> list[str]:
    """Monthly PDFs in the archive (year folders -> month folders -> PDF),
    optionally only from `min_year` on (a full listing is ~140 requests)."""
    pdfs = []
    for year_url in _links(CLIMATE_ROOT):
        m = re.search(r"/(\d{4})/$", year_url)
        if not m or (min_year and int(m.group(1)) < min_year):
            continue
        for month_url in _links(year_url):
            if month_url.lower().endswith(".pdf"):
                pdfs.append(month_url)
                continue
            pdfs += [u for u in _links(month_url) if u.lower().endswith(".pdf")]
    return pdfs


def pdf_month(url: str) -> tuple[int, int] | None:
    name = urllib.parse.unquote(url.rsplit("/", 1)[-1])
    m = re.search(r"_\s*(\d{1,2})\s*_\s*(\d{4})\s*\.pdf$", name, re.I)
    return (int(m.group(2)), int(m.group(1))) if m else None


def pdf_to_text(data: bytes, char_width: float = 4.0) -> str:
    """Layout text of a PDF, like `pdftotext -layout`, but the same on every machine.

    Some archive PDFs draw a table row twice on top of itself (a fake-bold effect);
    older poppler (Ubuntu 24.04) then scatters that row over several lines and the
    day is lost. pdfplumber's dedupe_chars() removes the overlay; words are placed
    back on a character grid by their x position so columns stay aligned."""
    import io

    import pdfplumber

    out = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            words = page.dedupe_chars().extract_words(keep_blank_chars=False, use_text_flow=False)
            rows: list[list[dict]] = []
            for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
                if rows and abs(rows[-1][0]["top"] - w["top"]) <= 3:
                    rows[-1].append(w)
                else:
                    rows.append([w])
            for row in rows:
                line, prev = "", None
                for w in sorted(row, key=lambda w: w["x0"]):
                    if prev is not None and w["x0"] - prev["x1"] < 6:
                        col = len(line) + 1  # same phrase ("Pafos Airport"): one space
                    else:  # next cell: place by x, at least two spaces apart
                        col = max(round(w["x0"] / char_width), len(line) + 2 if line else 0)
                    line = line.ljust(col) + w["text"]
                    prev = w
                out.append(line)
    return "\n".join(out)


def _cell(token: str | None):
    """-> (value, trace)"""
    if token is None:
        return None, 0
    t = token.strip().lower()
    if t in ("tr", "tr."):
        return 0.0, 1
    try:
        return float(t), 0
    except ValueError:
        return None, 0  # N.R., NR, -, dew marks...


KNOWN_NAMES = ["Pafos Airport", "Prodromos (CFC)", "Athalassa", "Larnaka Airport", "New Limassol Port",
               "Paralimni (St. Frenaros)", "Paralimni (Hosp.)", "Limassol (Public Garden)"]


def split_names(line: str) -> list[str]:
    """Station names are separated by 2+ spaces, except when the layout is tight
    and two land one space apart: such chunks are cut along known names."""
    out = []
    for chunk in (c.strip() for c in re.split(r"\s{2,}", line.strip()) if c.strip()):
        rest = chunk
        while rest:
            known = next((k for k in KNOWN_NAMES if rest.lower().startswith(k.lower())), None)
            if not known or len(known) == len(rest):
                out.append(rest)
                break
            out.append(rest[:len(known)])
            rest = rest[len(known):].strip()
    return out


def parse_climate(text: str, year: int, month: int) -> list[dict]:
    """Rows of one monthly table. Values are assigned to columns by horizontal
    position under the Max / Min / Rain header, so blank cells stay blank."""
    lines = text.splitlines()
    hdr_i = next((i for i, l in enumerate(lines) if re.match(r"^\s*Max\s+Min\s+Rain\b", l)), None)
    if hdr_i is None:
        return []
    header = lines[hdr_i]
    cols = [(m.start() + m.end()) / 2 for m in re.finditer(r"Max|Min|Rain", header)]
    names_line = next((lines[i] for i in range(hdr_i - 1, -1, -1)
                       if re.search(r"[A-Za-z]", lines[i]) and not re.search(r"Μεγ|Ημερ|Day", lines[i])), "")
    names = split_names(names_line)
    if len(names) * 3 != len(cols):
        log.warning("%04d-%02d: %d station names for %d columns, skipped", year, month, len(names), len(cols))
        return []

    rows = []
    for line in lines[hdr_i + 1:]:
        m = re.match(r"^\s*(\d{1,2})\s", line)
        if not m:
            if rows and re.search(r"Aver|Μέση", line):
                break  # monthly summary block
            continue
        day = int(m.group(1))
        try:
            date = datetime(year, month, day).strftime("%Y-%m-%d")
        except ValueError:
            continue
        cells: list[str | None] = [None] * len(cols)
        for tok in re.finditer(r"\S+", line[m.end():]):
            centre = m.end() + (tok.start() + tok.end()) / 2
            j = min(range(len(cols)), key=lambda k: abs(cols[k] - centre))
            cells[j] = tok.group(0)
        for s, name in enumerate(names):
            tmax, _ = _cell(cells[3 * s])
            tmin, _ = _cell(cells[3 * s + 1])
            rain, trace = _cell(cells[3 * s + 2])
            if tmax is None and tmin is None and rain is None:
                continue
            if tmax is not None and tmin is not None and tmax < tmin:
                log.warning("%s %s: max %s < min %s, columns misread?", name, date, tmax, tmin)
            rows.append({"name": name, "date": date, "tmax": tmax, "tmin": tmin, "rain": rain, "trace": trace})
    return rows


def station_code(name: str) -> str:
    key = name.strip().lower()
    return CLIMATE_CODES.get(key) or re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")


def store_climate(conn, rows: list[dict]) -> int:
    before = conn.total_changes
    with conn:
        for r in rows:
            code = station_code(r["name"])
            conn.execute("INSERT OR IGNORE INTO climate_stations (code, name) VALUES (?, ?)", (code, r["name"]))
            # preliminary months get corrected later: the newest file wins
            conn.execute(
                "INSERT OR REPLACE INTO climate_daily (station, date, tmax, tmin, rain, trace) VALUES (?, ?, ?, ?, ?, ?)",
                (code, r["date"], r["tmax"], r["tmin"], r["rain"], r["trace"]),
            )
    return conn.total_changes - before


def collect_climate(db_path: str, refresh_months: int = 2, pause: float = 1.0) -> int:
    """Download archive PDFs not seen yet, plus the last `refresh_months` months
    (still preliminary, re-published as data is checked)."""
    conn = connect(db_path)
    try:
        seen = {r["url"] for r in conn.execute("SELECT url FROM climate_files")}
        today = datetime.now(weather.LOCAL_TZ)
        recent = {((today.year * 12 + today.month - 1 - k) // 12, (today.year * 12 + today.month - 1 - k) % 12 + 1)
                  for k in range(refresh_months)}
        total = 0
        # after the first full backfill only the current and previous year can change
        for url in archive_pdfs(min_year=today.year - 1 if seen else None):
            ym = pdf_month(url)
            if not ym or (url in seen and ym not in recent):
                continue
            try:
                rows = parse_climate(pdf_to_text(get(url, timeout=60)), *ym)
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                log.warning("%s: %s", url, exc)
                continue
            n = store_climate(conn, rows)
            with conn:
                conn.execute("INSERT OR REPLACE INTO climate_files (url, year, month, fetched, rows) VALUES (?, ?, ?, ?, ?)",
                             (url, ym[0], ym[1], int(time.time()), len(rows)))
            total += n
            log.info("%04d-%02d: %d rows", ym[0], ym[1], len(rows))
            time.sleep(pause)
        log.info("climate archive: %d rows written", total)
        return total
    finally:
        conn.close()


# ---------- reads for the dashboard ----------
# the dashboard opens the database read-only, possibly before this collector ever
# created its tables: a missing table reads as "no data"

def _tolerant(empty):
    def wrap(fn):
        def inner(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except sqlite3.OperationalError as exc:
                if "no such table" in str(exc):
                    return empty() if callable(empty) else empty
                raise
        inner.__name__ = fn.__name__
        return inner
    return wrap


def radar_status(conn) -> dict:
    try:
        return {r["image"]: r["updated"] for r in conn.execute("SELECT image, updated FROM radar")}
    except sqlite3.OperationalError:
        return {}


def _active_alerts_safe(conn) -> list[dict]:
    try:
        return active_alerts(conn)
    except sqlite3.OperationalError:
        return []


@_tolerant(lambda: {"forecast": None, "sst": {"ts": [], "sst": []}, "warnings": None, "radar": {}, "alerts": []})
def marine(conn) -> dict:
    latest = conn.execute("SELECT * FROM sea_forecasts ORDER BY issued DESC, valid_from DESC LIMIT 1").fetchone()
    sst = conn.execute(
        "SELECT valid_from AS ts, sst FROM sea_forecasts WHERE sst IS NOT NULL ORDER BY valid_from"
    ).fetchall()
    warn = conn.execute("SELECT ts, text FROM warnings ORDER BY ts DESC LIMIT 1").fetchone()
    out = dict(latest) if latest else None
    if out and out.get("areas"):
        out["areas"] = json.loads(out["areas"])
    return {
        "forecast": out,
        "sst": {"ts": [r["ts"] for r in sst], "sst": [r["sst"] for r in sst]},
        "warnings": dict(warn) if warn else None,
        "radar": radar_status(conn),
        "alerts": _active_alerts_safe(conn),
    }


@_tolerant(list)
def climate_stations(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT s.code, s.name, MIN(d.date) AS first, MAX(d.date) AS last, COUNT(*) AS days"
        " FROM climate_stations s JOIN climate_daily d ON d.station = s.code GROUP BY s.code ORDER BY s.name"
    )]


@_tolerant(lambda: {"ts": [], "tmax": [], "tmin": [], "rain": []})
def climate_readings(conn, station: str, ts_from: int | None = None, ts_to: int | None = None) -> dict:
    day = lambda ts: datetime.fromtimestamp(ts, weather.LOCAL_TZ).strftime("%Y-%m-%d")
    sql, args = "SELECT date, tmax, tmin, rain FROM climate_daily WHERE station = ?", [station]
    if ts_from is not None:
        sql += " AND date >= ?"
        args.append(day(ts_from))
    if ts_to is not None:
        sql += " AND date <= ?"
        args.append(day(ts_to))
    rows = conn.execute(sql + " ORDER BY date", args).fetchall()
    midnight = lambda d: int(datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=weather.LOCAL_TZ).timestamp())
    return {
        "ts": [midnight(r["date"]) for r in rows],
        "tmax": [r["tmax"] for r in rows],
        "tmin": [r["tmin"] for r in rows],
        "rain": [r["rain"] for r in rows],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Sea forecasts, warnings and the climate archive from dom.org.cy")
    parser.add_argument("what", choices=["forecast", "climate"])
    parser.add_argument("--config", help="config.env path (default: ./config.env)")
    parser.add_argument("--db", help="SQLite path, overrides ARANET_WEATHER_DB")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    db_path = args.db or get_settings(args.config).weather_db
    try:
        if args.what == "forecast":
            collect_forecast(db_path)
        else:
            collect_climate(db_path)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        log.error("failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
