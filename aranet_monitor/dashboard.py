"""Dashboard: a tiny stdlib HTTP server with a JSON API and a static ECharts page.

GET /weather/home         -> static/index.html (home sensor); "/" redirects here
GET /weather/outdoor      -> static/weather.html (Cyprus weather stations); "/weather" redirects here
GET /weather/outdoor/forecast -> static/forecast.html (forecast bulletins, translated)
GET /api/weather/forecast -> latest bulletins A/B/C with Russian translations
GET /api/weather/uv?station=CODE&from=T&to=T -> hourly CAMS UV index (incl. forecast hours)
GET /api/weather/air?station=CODE&from=T&to=T -> hourly air quality: CAMS at the station + nearest DLI measurements
GET /static/<file>        -> static assets (echarts is vendored, works offline)
GET /api/readings?hours=N -> readings for the last N hours (no param: everything)
GET /api/readings?from=T&to=T -> readings in [from, to], unix seconds, either bound optional
GET /api/latest           -> latest reading + device status
GET /api/pressure-offset  -> how far the home sensor reads below sea-level pressure (hPa), from nearby stations
GET /api/weather/stations -> stations with coordinates and latest observation
GET /api/weather/readings?station=CODE&from=T&to=T -> one station's observations (+ NET "feels like")
GET /api/weather/marine   -> latest sea forecast, sea surface temperature history, current warnings
GET /api/weather/climate/stations -> stations in the daily archive (since 2016)
GET /api/weather/climate?station=CODE&from=T&to=T -> daily Tmax / Tmin / rain
"""

import argparse
import json
import logging
import mimetypes
import sys
import time
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import air, db, dom, forecast, uv, weather
from .config import get_settings

STATIC_DIR = Path(__file__).parent / "static"
# old addresses keep working
REDIRECTS = {"/": "/weather/home", "/index.html": "/weather/home", "/weather": "/weather/outdoor",
             "/weather/": "/weather/outdoor", "/weather.html": "/weather/outdoor"}
log = logging.getLogger("aranet.dashboard")


class Handler(BaseHTTPRequestHandler):
    def __init__(self, *args, db_path: str, weather_db: str, **kwargs):
        self.db_path = db_path
        self.weather_db = weather_db
        super().__init__(*args, **kwargs)

    def log_message(self, fmt, *args):
        log.debug("%s - %s", self.address_string(), fmt % args)

    def do_GET(self):
        url = urlparse(self.path)
        try:
            if url.path in REDIRECTS:
                self._redirect(REDIRECTS[url.path] + (f"?{url.query}" if url.query else ""))
            elif url.path in ("/weather/home", "/weather/home/"):
                self._file(STATIC_DIR / "index.html")
            elif url.path.startswith("/static/"):
                self._static(url.path[len("/static/"):])
            elif url.path == "/api/readings":
                self._readings(parse_qs(url.query))
            elif url.path == "/api/latest":
                self._json(self._with_db(db.latest))
            elif url.path == "/api/pressure-offset":
                self._json(pressure_offset(self.db_path, self.weather_db))
            elif url.path in ("/weather/outdoor", "/weather/outdoor/"):
                self._file(STATIC_DIR / "weather.html")
            elif url.path in ("/weather/outdoor/forecast", "/weather/outdoor/forecast/"):
                self._file(STATIC_DIR / "forecast.html")
            elif url.path in ("/api/weather/uv", "/api/weather/air"):
                q = parse_qs(url.query)
                station = q.get("station", [""])[0]
                if not station:
                    raise ValueError("station is required")
                fn = uv.readings if url.path.endswith("/uv") else air.readings
                self._json(self._with_weather(fn, station, *self._range(q)))
            elif url.path == "/api/weather/forecast":
                self._json(self._with_weather(forecast.latest))
            elif url.path == "/api/weather/stations":
                self._json(self._with_weather(weather.station_list))
            elif url.path == "/api/weather/readings":
                self._weather_readings(parse_qs(url.query))
            elif url.path == "/api/weather/marine":
                self._json(self._with_weather(dom.marine))
            elif url.path == "/api/weather/climate/stations":
                self._json(self._with_weather(dom.climate_stations))
            elif url.path == "/api/weather/climate":
                q = parse_qs(url.query)
                station = q.get("station", [""])[0]
                if not station:
                    raise ValueError("station is required")
                self._json(self._with_weather(dom.climate_readings, station, *self._range(q)))
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def _redirect(self, location: str):
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _with_db(self, fn, *args):
        conn = db.connect_readonly(self.db_path)
        try:
            return fn(conn, *args)
        finally:
            conn.close()

    def _with_weather(self, fn, *args):
        conn = db.connect_readonly(self.weather_db, empty=dom.connect)
        try:
            return fn(conn, *args)
        finally:
            conn.close()

    @staticmethod
    def _range(query):
        def param(name):
            v = query.get(name, [None])[0]
            return None if v in (None, "", "all") else v

        hours, ts_from, ts_to = param("hours"), param("from"), param("to")
        if hours is not None:
            h = float(hours)
            if h <= 0:
                raise ValueError("hours must be positive")
            ts_from = int(time.time() - h * 3600)
        else:
            ts_from = int(ts_from) if ts_from is not None else None
        ts_to = int(ts_to) if ts_to is not None else None
        if ts_from is not None and ts_to is not None and ts_from > ts_to:
            raise ValueError("from must be <= to")
        return ts_from, ts_to

    def _weather_readings(self, query):
        station = query.get("station", [""])[0]
        if not station:
            raise ValueError("station is required")
        self._json(self._with_weather(weather.readings, station, *self._range(query)))

    def _readings(self, query):
        rows = self._with_db(db.fetch_readings, *self._range(query))
        # columnar: smaller payload and maps straight onto echarts series
        self._json({
            "ts": [r["ts"] for r in rows],
            "co2": [r["co2"] for r in rows],
            "temperature": [r["temperature"] for r in rows],
            "humidity": [r["humidity"] for r in rows],
            "pressure": [r["pressure"] for r in rows],
        })

    def _static(self, name: str):
        path = (STATIC_DIR / name).resolve()
        if STATIC_DIR.resolve() not in path.parents or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self._file(path)

    def _file(self, path: Path):
        body = path.read_bytes()
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") or ctype.endswith("javascript") else ""))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=3600" if path.name.endswith(".min.js") else "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def pressure_offset(db_path: str, weather_db: str, days: int = 7, max_gap: int = 400) -> dict:
    """Sea-level pressure minus the home reading, in hPa: the sensor measures at the
    flat's altitude (~8 m per hPa). Median over stations that report sea-level
    pressure (MSL or QNH) of the median paired difference, so one odd station or a
    passing front doesn't move it."""
    import bisect
    import statistics

    since = int(time.time()) - days * 86400
    home_conn = db.connect_readonly(db_path)
    wx_conn = db.connect_readonly(weather_db, empty=dom.connect)
    try:
        home = home_conn.execute("SELECT ts, pressure FROM readings WHERE ts >= ? AND pressure IS NOT NULL ORDER BY ts", (since,)).fetchall()
        ts = [r[0] for r in home]
        per_station = []
        rows = wx_conn.execute(
            "SELECT station, ts, COALESCE(p_msl, p_qnh) FROM observations"
            " WHERE ts >= ? AND COALESCE(p_msl, p_qnh) IS NOT NULL ORDER BY station, ts", (since,)).fetchall()
        by_station: dict[str, list[float]] = {}
        for station, t, p in rows:
            i = bisect.bisect_left(ts, t)
            best = min((j for j in (i - 1, i) if 0 <= j < len(ts)), key=lambda j: abs(ts[j] - t), default=None)
            if best is not None and abs(ts[best] - t) <= max_gap:
                by_station.setdefault(station, []).append(p - home[best][1])
        per_station = [statistics.median(d) for d in by_station.values() if len(d) >= 3]
    finally:
        home_conn.close()
        wx_conn.close()
    if not per_station:
        return {"offset": None, "stations": 0}
    return {"offset": round(statistics.median(per_station), 1), "stations": len(per_station)}


def make_server(host: str, port: int, db_path: str, weather_db: str = "data/weather.db") -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), partial(Handler, db_path=db_path, weather_db=weather_db))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Aranet4 dashboard web server")
    parser.add_argument("--config", help="config.env path (default: ./config.env)")
    parser.add_argument("--db", help="SQLite path, overrides ARANET_DB")
    parser.add_argument("--host", help="bind address, overrides ARANET_HOST")
    parser.add_argument("--port", type=int, help="port, overrides ARANET_PORT")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    s = get_settings(args.config)
    server = make_server(args.host or s.host, args.port or s.port, args.db or s.db_path, s.weather_db)
    log.info("dashboard on http://%s:%d (db %s)", *server.server_address[:2], args.db or s.db_path)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
