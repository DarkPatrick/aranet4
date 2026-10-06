"""An LLM's own forecast for the next 4 / 12 / 24 hours, from everything the dashboard holds.

The prompt carries the last 24 h of observations (15 key stations hourly, the rest
every 3 h), the weather service's bulletins and warnings, lightning by hour and place,
and the ECMWF IFS forecast (9 km, via Open-Meteo) for the key stations: the next 24 h
hourly and the past 24 h every 3 h, so the model can see where ECMWF was off yesterday.
The answer is JSON (a schema the CLI enforces), saved as <issued>.json and latest.json in
ARANET_AI_DIR, which the dashboard serves.

The model runs through a command line tool reading the prompt on stdin, by default Codex
(ARANET_AI_CMD overrides it). The CLI's login belongs to a person's account, so this may
run as another user than the dashboard: with --api it reads everything through the
dashboard's own HTTP API instead of the database files.

    aranet-ai-forecast                               # from the local databases
    aranet-ai-forecast --api http://127.0.0.1:8091   # from a running dashboard
    aranet-ai-forecast --dry-run                     # print the prompt only
"""

import argparse
import csv
import io
import json
import logging
import os
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from . import agg, dom, forecast, weather
from .config import get_settings

log = logging.getLogger("aranet.ai")

KEY_STATIONS = ["ATHALASSA", "ASTROMERITIS", "ATHIENOU", "LCLK", "CAVO_GRECO", "FRENAROS", "ZYGI", "LIMASSOL",
                "KOURIS", "LCPH", "KATHIKAS", "POLIS", "KPYRGOS", "PRODROMOS", "TROODOS"]
MY_PLACE = "Твоё место"
LOCATION_FILE = "viewer_location.json"   # next to the weather database; the dashboard writes it
LOCATION_MAX_AGE = 3 * 86400             # an older location isn't "where you are" any more
REGIONS = ["Никосия и центральная равнина", "Ларнака и восток", "Лимассол и южное побережье",
           "Пафос и запад", "северо-запад (Полис, Като Пиргос)", "Троодос (горы)"]
ECMWF_VARS = ["temperature_2m", "relative_humidity_2m", "precipitation", "cloud_cover", "cape",
              "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m", "weather_code"]
DEFAULT_CMD = ("codex exec --model gpt-6-sol --skip-git-repo-check --ephemeral --sandbox read-only "
               "--ignore-rules --color never")
UA = {"User-Agent": "aranet-monitor/0.1 (+https://github.com/DarkPatrick/aranet4)"}


# the answer's shape; strict (every field required, nothing extra), as structured outputs want
def make_schema(personal: bool) -> dict:
    regions = [MY_PLACE] + REGIONS if personal else REGIONS
    region = {"type": "object", "additionalProperties": False,
              "required": ["region", "temp_min", "temp_max", "precip_chance", "thunder_chance", "wind", "notes"],
              "properties": {"region": {"type": "string", "enum": regions},
                             "temp_min": {"type": "number"}, "temp_max": {"type": "number"},
                             "precip_chance": {"type": "integer", "minimum": 0, "maximum": 100},
                             "thunder_chance": {"type": "integer", "minimum": 0, "maximum": 100},
                             "wind": {"type": "string"}, "notes": {"type": "string"}}}
    horizon = {"type": "object", "additionalProperties": False,
               "required": ["hours", "valid_until", "overview", "confidence", "regions"],
               "properties": {"hours": {"type": "integer", "enum": [4, 12, 24]},
                              "valid_until": {"type": "string"}, "overview": {"type": "string"},
                              "confidence": {"type": "string", "enum": ["низкая", "средняя", "высокая"]},
                              "regions": {"type": "array", "items": region, "minItems": len(regions), "maxItems": len(regions)}}}
    return {"type": "object", "additionalProperties": False,
            "required": ["place", "summary", "situation", "model_vs_obs", "horizons", "risks"],
            "properties": {"place": {"type": "string"}, "summary": {"type": "string"}, "situation": {"type": "string"},
                           "model_vs_obs": {"type": "string"},
                           "horizons": {"type": "array", "items": horizon, "minItems": 3, "maxItems": 3},
                           "risks": {"type": "array", "items": {"type": "string"}}}}


ANSWER_SCHEMA = make_schema(False)

INSTRUCTIONS = f"""Ты — синоптик, который готовит прогноз погоды для Кипра. Отвечай по-русски.
Ниже все данные, которые есть: наблюдения 55 автоматических станций метеослужбы Кипра за последние 24 часа,
молнии со спутника Meteosat-12, текстовые бюллетени и предупреждения метеослужбы и прогноз модели ECMWF IFS
(9 км) для 15 ключевых станций — на 24 часа вперёд и за прошедшие сутки (чтобы сравнить модель с фактом).

Задача: свой прогноз на ближайшие 4, 12 и 24 часа от момента выпуска по шести районам: {", ".join(REGIONS)}.
- Сравни прогноз ECMWF за прошедшие сутки с наблюдениями на тех же станциях и учти систематические ошибки
  модели (температура, ветер, осадки) — опиши их в model_vs_obs.
- Для ближайших 4 часов больше опирайся на текущие наблюдения и их тренды (суточный ход, бриз, куда смещаются
  грозовые очаги по молниям и ветру), дальше — на модель с поправками и на бюллетени метеослужбы.
- temp_min/temp_max — диапазон температуры воздуха в районе за горизонт (от момента выпуска до его конца).
- precip_chance и thunder_chance — вероятность в процентах, что в районе за этот горизонт будут осадки ≥ 0,2 мм
  и гроза. Будь откалиброван: не завышай и не занижай.
- confidence — насколько уверен; если данные противоречат друг другу, скажи об этом в overview.
- situation — коротко синоптическая ситуация (что происходит и почему), summary — 1–2 предложения для человека.
- risks — заметные риски (грозы, ливни, сильный ветер, жара, туман), пустой список, если их нет.
- place — пустая строка (место пользователя не задано).
Не запускай никаких команд и не ищи ничего вне этого сообщения: всё нужное — ниже.
Время везде местное (Кипр, Asia/Nicosia).
"""

PERSONAL = f"""
ВАЖНО: прогноз читает человек, который сейчас находится в точке из раздела «Место пользователя».
- summary, situation и overview каждого горизонта начинай с того, что ждёт именно в этой точке
  (температура, дождь, гроза, ветер по часам, когда что начнётся), и только потом — общее по Кипру.
- В regions первой строкой дай «{MY_PLACE}»: прогноз для этой точки (вероятности — для самой точки, а не для района,
  поэтому обычно ниже районных), затем шесть районов как обычно.
- Учти высоту и удалённость от моря точки по ближайшим станциям и ECMWF для неё (строки МОЁ_МЕСТО), поправленный
  на ошибки модели, которые ты видишь на соседних станциях.
- place — короткое название места по-русски (город или посёлок рядом, например «Лимассол, Гермасойя»).
"""


class DbSource:
    """The dashboard's data straight from the database files."""

    def __init__(self, weather_db: str, lightning_db: str):
        from . import db
        self.weather_db = weather_db
        self.conn = db.connect_readonly(weather_db, empty=dom.connect)
        self.lconn = db.connect_readonly(lightning_db, empty=lambda p: sqlite3.connect(p)) if Path(lightning_db).exists() else None

    def stations(self):
        return [(r[0], r[1], r[2]) for r in self.conn.execute("SELECT code, lat, lon FROM stations ORDER BY code")]

    def readings(self, code, lo, hi):
        return weather.readings(self.conn, code, lo, hi)

    def lightning(self, lo):
        try:
            rows = self.lconn.execute("SELECT ts, lat, lon FROM flashes WHERE ts > ?", (lo,)).fetchall() if self.lconn else []
        except sqlite3.OperationalError:
            rows = []
        return {"ts": [r[0] for r in rows], "lat": [r[1] for r in rows], "lon": [r[2] for r in rows]}

    def forecast(self):
        return forecast.latest(self.conn)

    def marine(self):
        return dom.marine(self.conn)

    def location(self):
        return read_location(Path(self.weather_db).parent)


class ApiSource:
    """The same through a running dashboard's HTTP API (another user can't read its files)."""

    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def _get(self, path: str):
        with urllib.request.urlopen(self.base + path, timeout=60) as resp:
            return json.loads(resp.read())

    def stations(self):
        return [(s["code"], s["lat"], s["lon"]) for s in self._get("/api/weather/stations")]

    def readings(self, code, lo, hi):
        return self._get(f"/api/weather/readings?station={urllib.parse.quote(code)}&from={lo}&to={hi}")

    def lightning(self, lo):
        return self._get(f"/api/weather/lightning?from={lo}")

    def forecast(self):
        return self._get("/api/weather/forecast")

    def marine(self):
        return self._get("/api/weather/marine")

    def location(self):
        return self._get("/api/weather/location")


def read_location(directory) -> dict | None:
    """The viewer's last location the dashboard got from a browser: {lat, lon, ts}, if recent."""
    try:
        loc = json.loads((Path(directory) / LOCATION_FILE).read_text())
    except (OSError, ValueError):
        return None
    return loc if time.time() - loc.get("ts", 0) <= LOCATION_MAX_AGE else None


def write_location(directory, lat: float, lon: float) -> dict:
    loc = {"lat": round(float(lat), 4), "lon": round(float(lon), 4), "ts": int(time.time())}
    p = Path(directory) / LOCATION_FILE
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(loc))
    tmp.chmod(0o644)
    os.replace(tmp, p)
    return loc


def _km(lat1, lon1, lat2, lon2) -> float:
    import math
    r = math.pi / 180
    a = math.sin((lat2 - lat1) * r / 2) ** 2 + math.cos(lat1 * r) * math.cos(lat2 * r) * math.sin((lon2 - lon1) * r / 2) ** 2
    return 12742 * math.asin(math.sqrt(a))


def nearest_stations(src, lat, lon, n=4) -> list[tuple[str, float]]:
    return sorted(((c, _km(lat, lon, la, lo)) for c, la, lo in src.stations()), key=lambda x: x[1])[:n]


def _local(ts: float) -> str:
    return datetime.fromtimestamp(ts, weather.LOCAL_TZ).strftime("%d.%m %H:%M")


def _fmt(v, n=1):
    return "" if v is None else round(v, n)


def observations_csv(src, now: int, hourly=frozenset(KEY_STATIONS)) -> str:
    """Key stations (and those nearest the viewer) hourly, the rest every 3 h, over the last 24 h."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["station", "time", "t_C", "rh_%", "rain_mm", "wind_ms", "gust_ms", "dir_deg", "p_msl_hPa", "rad_Wm2"])
    for code, _, _ in src.stations():
        d = src.readings(code, now - 86400, now)
        if not d["ts"]:
            continue
        wind = [a if a is not None else b for a, b in zip(d["wind10"], d["wind2"])]
        cols = {"ts": d["ts"], "temp": d["temp"], "rh": d["rh"], "rain": d["rain"], "wind": wind, "wdir": d["wdir"],
                "p_msl": d["p_msl"], "rad": d["rad_global"]}
        h = agg.aggregate(cols, "hour", sums={"rain"}, circular={"wdir"})
        gust = agg.aggregate({"ts": d["ts"], "wind": wind}, "hour", maxes={"wind"})["wind"]
        if code in hourly:
            rows = list(zip(h["ts"], h["temp"], h["rh"], h["rain"], h["wind"], gust, h["wdir"], h["p_msl"], h["rad"]))
        else:  # three-hour steps from the hourly values
            rows = []
            for i in range(0, len(h["ts"]), 3):
                sl = slice(i, i + 3)
                vals = lambda k: [x for x in h[k][sl] if x is not None]
                mean = lambda k: sum(vals(k)) / len(vals(k)) if vals(k) else None
                rows.append((h["ts"][i], mean("temp"), mean("rh"), sum(vals("rain")) if vals("rain") else None,
                             mean("wind"), max([x for x in gust[sl] if x is not None], default=None),
                             h["wdir"][i], mean("p_msl"), mean("rad")))
        for ts, t, rh, rain, wnd, g, dr, p, rad in rows:
            w.writerow([code, _local(ts), _fmt(t), _fmt(rh, 0), _fmt(rain), _fmt(wnd), _fmt(g), _fmt(dr, 0), _fmt(p), _fmt(rad, 0)])
    return buf.getvalue()


def lightning_csv(src, now: int) -> str:
    """Flashes per hour per ~0.5 degree cell (lat, lon of the cell centre)."""
    try:
        d = src.lightning(now - 86400)
    except (OSError, ValueError):
        return "нет данных"
    cells: dict = {}
    for t, la, lo in zip(d["ts"], d["lat"], d["lon"]):
        key = (int(t // 3600) * 3600, round(la * 2) / 2, round(lo * 2) / 2)
        cells[key] = cells.get(key, 0) + 1
    if not cells:
        return "за последние 24 часа молний в районе Кипра (±150 км) не было"
    return "hour_start,lat,lon,flashes\n" + "\n".join(f"{_local(h)},{la},{lo},{n}" for (h, la, lo), n in sorted(cells.items()))


def ecmwf_csv(src, place=None) -> str:
    """ECMWF IFS at the key stations (and the viewer's place, as МОЁ_МЕСТО): the past 24 h every 3 h,
    the next 24 h hourly."""
    st = {c: (la, lo) for c, la, lo in src.stations()}
    codes = [c for c in KEY_STATIONS if c in st]
    if place:
        st["МОЁ_МЕСТО"] = (place["lat"], place["lon"])
        codes = ["МОЁ_МЕСТО"] + codes
    params = {"latitude": ",".join(f"{st[c][0]:.4f}" for c in codes),
              "longitude": ",".join(f"{st[c][1]:.4f}" for c in codes),
              "models": "ecmwf_ifs", "hourly": ",".join(ECMWF_VARS), "past_days": 1, "forecast_days": 2,
              "timezone": "GMT", "timeformat": "unixtime", "wind_speed_unit": "ms"}
    req = urllib.request.Request("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params), headers=UA)
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    data = data if isinstance(data, list) else [data]
    now = time.time()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["station", "time", "t_C", "rh_%", "precip_mm", "cloud_%", "cape_Jkg", "wind_ms", "dir_deg", "gust_ms", "wmo_code"])
    for code, loc in zip(codes, data):
        h = loc.get("hourly", {})
        for i, ts in enumerate(h.get("time", [])):
            past = ts <= now
            if past and (ts < now - 86400 or datetime.fromtimestamp(ts, weather.LOCAL_TZ).hour % 3):
                continue
            if not past and ts > now + 86400:
                continue
            w.writerow([code, _local(ts)] + [_fmt(h[v][i], 1) for v in ECMWF_VARS])
    return buf.getvalue()


def bulletins_text(src) -> str:
    out = []
    for b in src.forecast().get("bulletins", []):
        text = " ".join(p.get("ru") or p.get("el") or "" for p in b.get("paragraphs", []))
        obs = "; ".join(f"{o['place']} {o.get('tmax')}/{o.get('tmin')}°C" for o in b.get("observed", []))
        out.append(f"Бюллетень {b['issue']}, выпущен {_local(b['issued'])}, действует {_local(b['valid_from'])}–"
                   f"{_local(b['valid_to'])}: {text}" + (f" Факт за день: {obs}." if obs else ""))
    return "\n\n".join(out) or "нет"


def warnings_text(src) -> str:
    m = src.marine()
    lines = [f"{a.get('level')} {a.get('event')} {a.get('onset')}–{a.get('expires')}: {a.get('description_en') or a.get('headline') or ''}"
             for a in m.get("alerts") or []]
    sea = m.get("forecast") or {}
    if sea:
        lines.append("Морской прогноз: " + json.dumps({k: sea.get(k) for k in ("issued", "overview", "sst", "warnings") if k in sea},
                                                        ensure_ascii=False, default=str))
    return "\n".join(lines) or "действующих предупреждений нет"


def build_prompt(src, now: int | None = None, place=None) -> str:
    """`place`: the viewer's {lat, lon, ts} (the forecast then leads with it), or None."""
    now = int(now or time.time())
    meta = "\n".join(f"{c},{la:.3f},{lo:.3f}" for c, la, lo in src.stations())
    near = nearest_stations(src, place["lat"], place["lon"]) if place else []
    hourly = frozenset(KEY_STATIONS) | {c for c, _ in near}
    sections = [INSTRUCTIONS + (PERSONAL if place else ""), f"Время выпуска прогноза: {_local(now)}."]
    if place:
        sections.append(f"## Место пользователя\nКоординаты {place['lat']:.4f}, {place['lon']:.4f} (получены {_local(place['ts'])}). "
                        "Ближайшие станции (их наблюдения ниже — по часам): "
                        + ", ".join(f"{c} {km:.1f} км" for c, km in near) + ". Прогноз ECMWF для самой точки — строки МОЁ_МЕСТО.")
    sections += [
        f"## Станции (код, широта, долгота); ключевые: {', '.join(KEY_STATIONS)}\n{meta}",
        f"## Наблюдения за 24 часа (ключевые станции и ближайшие к пользователю — по часам, остальные с шагом 3 часа)\n{observations_csv(src, now, hourly)}",
        f"## Молнии за 24 часа (вспышек в час по ячейкам 0,5°)\n{lightning_csv(src, now)}",
        f"## ECMWF IFS для ключевых станций{' и точки пользователя' if place else ''} (прошлые сутки с шагом 3 ч, затем 24 ч вперёд по часам; wmo_code — код погоды WMO)\n{ecmwf_csv(src, place)}",
        f"## Бюллетени метеослужбы Кипра\n{bulletins_text(src)}",
        f"## Предупреждения и море\n{warnings_text(src)}",
    ]
    return "\n\n".join(sections)


def ask(prompt: str, cmd: str, schema_doc: dict = ANSWER_SCHEMA) -> tuple[dict, float]:
    """Run the CLI in an empty directory (no project files to wander into); JSON answer -> dict."""
    with tempfile.TemporaryDirectory() as tmp:
        schema, out = Path(tmp) / "schema.json", Path(tmp) / "answer.json"
        schema.write_text(json.dumps(schema_doc, ensure_ascii=False))
        args = shlex.split(cmd)
        if args and Path(args[0]).name == "codex":
            args += ["-C", tmp, "--output-schema", str(schema), "-o", str(out), "-"]
        started = time.time()
        res = subprocess.run(args, input=prompt, capture_output=True, text=True, timeout=1800, cwd=tmp)
        took = time.time() - started
        if res.returncode != 0:
            raise RuntimeError(f"{args[0]} exited {res.returncode}: {res.stderr[-2000:]}")
        text = out.read_text() if out.exists() else res.stdout
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1]), took


def save(ai_dir: str, issued: int, model: str, answer: dict, prompt_chars: int, seconds: float, place=None) -> Path:
    """<issued>.json plus latest.json (replaced atomically), readable by the dashboard's user."""
    d = Path(ai_dir)
    d.mkdir(parents=True, exist_ok=True)
    doc = {"issued": issued, "model": model, "seconds": round(seconds, 1), "prompt_chars": prompt_chars,
           "location": place, "forecast": answer}
    body = json.dumps(doc, ensure_ascii=False)
    path = d / f"{issued}.json"
    path.write_text(body)
    tmp = d / "latest.json.tmp"
    tmp.write_text(body)
    for p in (path, tmp):
        p.chmod(0o644)
    os.replace(tmp, d / "latest.json")
    return path


def latest(ai_dir: str) -> dict | None:
    try:
        return json.loads((Path(ai_dir) / "latest.json").read_text())
    except (OSError, ValueError):
        return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="An LLM forecast for the next 4 / 12 / 24 h from the dashboard's data")
    parser.add_argument("--config", help="config.env path (default: ./config.env)")
    parser.add_argument("--dry-run", action="store_true", help="print the prompt and exit")
    parser.add_argument("--cmd", help="the model CLI (default: ARANET_AI_CMD, else Codex with gpt-6-sol)")
    parser.add_argument("--api", help="read through a running dashboard (e.g. http://127.0.0.1:8091) instead of the files")
    parser.add_argument("--out", help="where to save the answers (default: ARANET_AI_DIR, else data/ai)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    s = get_settings(args.config)
    src = ApiSource(args.api) if args.api else DbSource(s.weather_db, s.lightning_db)
    now = int(time.time())
    try:
        place = src.location()
    except (OSError, ValueError):
        place = None
    prompt = build_prompt(src, now, place)
    if args.dry_run:
        print(prompt)
        return 0
    cmd = args.cmd or os.environ.get("ARANET_AI_CMD") or DEFAULT_CMD
    log.info("prompt: %d chars; asking %s", len(prompt), shlex.split(cmd)[0])
    answer, took = ask(prompt, cmd, make_schema(bool(place)))
    model = next((a for a in shlex.split(cmd) if a.startswith("gpt") or a.startswith("claude")), shlex.split(cmd)[0])
    path = save(args.out or s.ai_dir, now, model, answer, len(prompt), took, place)
    print(json.dumps(answer, ensure_ascii=False, indent=2))
    log.info("saved %s", path)
    log.info("done in %.0f s", took)
    return 0


if __name__ == "__main__":
    sys.exit(main())
