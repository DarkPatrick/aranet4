"""Public weather forecast bulletins of the Cyprus Department of Meteorology.

Three issues a day (A 05:00, B 11:00, C 16:00 local), Greek text only, at
dom.org.cy/FORECAST/public_{a,b,c}.html; the same issues also exist as English
table images (table_{a,b,c}_en.png) that the dashboard shows as they are.

The text is translated to Russian sentence by sentence, each sentence once:
consecutive issues repeat most of their wording, which keeps the volume inside
the free translation quotas.

    aranet-forecast            # timer: every 30 minutes
"""

import argparse
import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from . import dom, weather
from .config import get_settings, load_env_file

log = logging.getLogger("aranet.forecast")

BULLETIN_URL = "https://www.dom.org.cy/FORECAST/public_{}.html"
ISSUES = {"a": "A", "b": "B", "c": "C"}
UA = {"User-Agent": "aranet-monitor/0.1 (+https://github.com/DarkPatrick/aranet4)"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS bulletins (
    issue      TEXT NOT NULL,       -- A / B / C
    issued     INTEGER NOT NULL,
    valid_from INTEGER,
    valid_to   INTEGER,
    outlook    TEXT,                -- "(with outlook for 3 days)"
    paragraphs TEXT,                -- JSON [greek paragraph, ...]
    observed   TEXT,                -- JSON [[place, tmax, tmin, rh], ...] (issue C)
    fetched    INTEGER,
    PRIMARY KEY (issue, issued)
);

CREATE TABLE IF NOT EXISTS translations (
    hash     TEXT PRIMARY KEY,      -- sha1 of the source sentence
    source   TEXT,
    target   TEXT,
    provider TEXT,
    created  INTEGER
);
"""

PLACES = {
    "Λευκωσία": "Никосия", "Αεροδρόμιο Λάρνακας": "Ларнака (аэропорт)", "Λεμεσός": "Лимассол",
    "Αεροδρόμιο Πάφου": "Пафос (аэропорт)", "Φρέναρος": "Френарос", "Πρόδρομος": "Продромос",
    "Πόλις Χρυσοχούς": "Полис", "Λάρνακα": "Ларнака", "Πάφος": "Пафос", "Παραλίμνι": "Паралимни",
}

# fixes for terms the MT engines get wrong in forecasts
GLOSSARY_RU = [
    (r"\bштормы\b", "грозы"), (r"\bшторма\b", "грозы"), (r"\bшторм\b", "гроза"),
    (r"\bштормов\b", "гроз"), (r"\bштормами\b", "грозами"),
    (r"\b[Вв] гроза\b", "Во время грозы"),
]


def connect(path: str) -> sqlite3.Connection:
    conn = dom.connect(path)
    conn.executescript(SCHEMA)
    return conn


# ---------- parsing ----------

def parse_bulletin(raw: bytes, issue: str) -> dict | None:
    lines = dom.html_text(raw)
    text = "\n".join(lines)
    period = re.search(r"ΑΠΟ\s+(\d{4})\s+(\d{2}/\d{2}/\d{4})\s+ΜΕΧΡΙ\s+(\d{4})\s+(\d{2}/\d{2}/\d{4})", text)
    t = re.search(r"Ώρα έκδοσης:\s*(\d{4})", text)
    d = re.search(r"Ημερομηνία:\s*(\d{2}/\d{2}/\d{4})", text)
    if not (period and t and d):
        return None
    start = next(i for i, l in enumerate(lines) if "ΜΕΧΡΙ" in l) + 1
    outlook = None
    if start < len(lines) and lines[start].startswith("("):
        outlook = lines[start]
        start += 1
    paragraphs, observed = [], []
    i = start
    while i < len(lines):
        l = lines[i]
        if l.startswith(("Ώρα έκδοσης", "Ημερομηνία")):
            break
        if l.endswith(":") and i + 3 < len(lines) and re.match(r"^-?\d+°C$", lines[i + 1]):
            num = lambda s: float(re.sub(r"[^\d.\-]", "", s)) if re.search(r"\d", s) else None
            observed.append([l[:-1], num(lines[i + 1]), num(lines[i + 2]), num(lines[i + 3])])
            i += 4
            continue
        if l in ("Μέγιστη Θερμοκρασία", "Ελάχιστη Θερμοκρασία", "Σχετική Υγρασία"):
            i += 1
            continue
        paragraphs.append(l)
        i += 1
    return {
        "issue": issue,
        "issued": dom._local_ts(d.group(1), t.group(1)),
        "valid_from": dom._local_ts(period.group(2), period.group(1)),
        "valid_to": dom._local_ts(period.group(4), period.group(3)),
        "outlook": outlook,
        "paragraphs": paragraphs,
        "observed": observed,
    }


def store_bulletin(conn, b: dict) -> bool:
    """True when this issue is new (not seen before)."""
    exists = conn.execute("SELECT 1 FROM bulletins WHERE issue = ? AND issued = ?", (b["issue"], b["issued"])).fetchone()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO bulletins (issue, issued, valid_from, valid_to, outlook, paragraphs, observed, fetched)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (b["issue"], b["issued"], b["valid_from"], b["valid_to"], b["outlook"],
             json.dumps(b["paragraphs"], ensure_ascii=False), json.dumps(b["observed"], ensure_ascii=False), int(time.time())))
    return not exists


# ---------- translation ----------

def sentences(paragraph: str) -> list[str]:
    parts = re.split(r"(?<=[.;!])\s+", paragraph.strip())
    return [p for p in parts if p]


def _http_json(url: str, timeout: int = 20):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as resp:
        return json.loads(resp.read())


def mymemory(text: str, target: str = "ru") -> str:
    """MyMemory: free, 5000 chars/day per IP (50 000 with MYMEMORY_EMAIL), 500 bytes per request."""
    params = {"q": text, "langpair": f"el|{target}"}
    if os.environ.get("MYMEMORY_EMAIL"):
        params["de"] = os.environ["MYMEMORY_EMAIL"]
    d = _http_json("https://api.mymemory.translated.net/get?" + urllib.parse.urlencode(params))
    out = (d.get("responseData") or {}).get("translatedText")
    if d.get("responseStatus") != 200 or d.get("quotaFinished") or not out or "MYMEMORY WARNING" in out.upper():
        raise RuntimeError(f"mymemory: {d.get('responseStatus')} {d.get('responseDetails') or out}")
    return out


def google(text: str, target: str = "ru") -> str:
    """Google Translate's public web endpoint (no key; unofficial): the fallback."""
    q = urllib.parse.urlencode({"client": "gtx", "sl": "el", "tl": target, "dt": "t", "q": text})
    d = _http_json("https://translate.googleapis.com/translate_a/single?" + q)
    return "".join(part[0] for part in d[0] if part and part[0])


# Google reads Greek forecasts noticeably better (MyMemory turns "weak winds" into
# "patients", "inland" into "interior"); MyMemory is the official free API, kept as fallback
PROVIDERS = [("google", google), ("mymemory", mymemory)]


def _fix(text: str) -> str:
    for pat, rep in GLOSSARY_RU:
        text = re.sub(pat, rep, text, flags=re.I)
    return text


def translate_sentence(conn, src: str, providers=None) -> tuple[str | None, str | None]:
    providers = PROVIDERS if providers is None else providers  # looked up per call, not frozen at import
    h = hashlib.sha1(src.encode()).hexdigest()
    row = conn.execute("SELECT target, provider FROM translations WHERE hash = ?", (h,)).fetchone()
    if row:
        return row[0], row[1]
    if len(src.encode()) > 480:  # MyMemory takes up to 500 bytes: split on commas
        pieces = re.split(r"(?<=,)\s+", src)
        if len(pieces) > 1:
            outs = [translate_sentence(conn, p, providers) for p in pieces]
            if all(o[0] for o in outs):
                joined, provider = " ".join(o[0] for o in outs), outs[0][1]
                # cache the whole sentence too: the dashboard looks sentences up whole
                with conn:
                    conn.execute("INSERT OR REPLACE INTO translations (hash, source, target, provider, created) VALUES (?, ?, ?, ?, ?)",
                                 (h, src, joined, provider, int(time.time())))
                return joined, provider
    for name, fn in providers:
        try:
            out = _fix(fn(src).strip())
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, RuntimeError, KeyError, IndexError) as exc:
            log.warning("%s: %s", name, exc)
            continue
        with conn:
            conn.execute("INSERT OR REPLACE INTO translations (hash, source, target, provider, created) VALUES (?, ?, ?, ?, ?)",
                         (h, src, out, name, int(time.time())))
        return out, name
    return None, None


def translate_paragraph(conn, paragraph: str) -> tuple[str | None, set]:
    outs, used = [], set()
    for s in sentences(paragraph):
        t, provider = translate_sentence(conn, s)
        if t is None:
            return None, used
        outs.append(t)
        used.add(provider)
    return " ".join(outs), used


# ---------- collection ----------

def collect(db_path: str) -> None:
    conn = connect(db_path)
    try:
        for key, issue in ISSUES.items():
            try:
                b = parse_bulletin(dom.get(BULLETIN_URL.format(key)), issue)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                log.warning("bulletin %s: %s", issue, exc)
                continue
            if not b:
                log.warning("bulletin %s: page not recognised", issue)
                continue
            new = store_bulletin(conn, b)
            missing = 0
            for p in b["paragraphs"] + ([o[0] for o in b["observed"] if o[0] not in PLACES]):
                if translate_paragraph(conn, p)[0] is None:
                    missing += 1
            log.info("bulletin %s issued %s%s, %d paragraph(s)%s", issue,
                     datetime.fromtimestamp(b["issued"], weather.LOCAL_TZ).strftime("%d.%m %H:%M"),
                     " (new)" if new else "", len(b["paragraphs"]), f", {missing} untranslated" if missing else "")
    finally:
        conn.close()


# ---------- reads for the dashboard ----------

def latest(conn) -> dict:
    """Newest issue of each bulletin with translations from the cache (never calls a translator)."""
    try:
        rows = conn.execute(
            "SELECT * FROM bulletins b WHERE issued = (SELECT MAX(issued) FROM bulletins WHERE issue = b.issue) ORDER BY issued DESC"
        ).fetchall()
    except sqlite3.OperationalError:
        return {"bulletins": []}

    def cached(src):
        r = conn.execute("SELECT target, provider FROM translations WHERE hash = ?",
                         (hashlib.sha1(src.encode()).hexdigest(),)).fetchone()
        return (r[0], r[1]) if r else (None, None)

    def para_ru(p):
        parts, providers = [], set()
        for s in sentences(p):
            t, prov = cached(s)
            if t is None:
                return None, providers
            parts.append(t)
            providers.add(prov)
        return " ".join(parts), providers

    out = []
    for r in rows:
        paragraphs, providers = [], set()
        for p in json.loads(r["paragraphs"] or "[]"):
            ru, prov = para_ru(p)
            providers |= prov
            paragraphs.append({"el": p, "ru": ru})
        observed = [{"place": PLACES.get(o[0]) or para_ru(o[0])[0] or o[0], "place_el": o[0],
                     "tmax": o[1], "tmin": o[2], "rh": o[3]} for o in json.loads(r["observed"] or "[]")]
        out.append({
            "issue": r["issue"], "issued": r["issued"], "valid_from": r["valid_from"], "valid_to": r["valid_to"],
            "outlook": r["outlook"], "paragraphs": paragraphs, "observed": observed,
            "providers": sorted(p for p in providers if p),
            "table_image": f"https://www.dom.org.cy/FORECAST/table_{r['issue'].lower()}_en.png",
        })
    return {"bulletins": out, "map_image": "https://www.dom.org.cy/FORECAST/map.png"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Forecast bulletins from dom.org.cy, translated to Russian")
    parser.add_argument("--config", help="config.env path (default: ./config.env)")
    parser.add_argument("--db", help="SQLite path, overrides ARANET_WEATHER_DB")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_env_file(args.config or os.environ.get("ARANET_CONFIG", "config.env"))
    collect(args.db or get_settings(args.config).weather_db)
    return 0


if __name__ == "__main__":
    sys.exit(main())
