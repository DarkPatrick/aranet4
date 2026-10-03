"""Dashboard: a tiny stdlib HTTP server with a JSON API and a static ECharts page.

GET /                     -> static/index.html
GET /static/<file>        -> static assets (echarts is vendored, works offline)
GET /api/readings?hours=N -> readings for the last N hours (no param: everything)
GET /api/readings?from=T&to=T -> readings in [from, to], unix seconds, either bound optional
GET /api/latest           -> latest reading + device status
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

from . import db
from .config import get_settings

STATIC_DIR = Path(__file__).parent / "static"
log = logging.getLogger("aranet.dashboard")


class Handler(BaseHTTPRequestHandler):
    def __init__(self, *args, db_path: str, **kwargs):
        self.db_path = db_path
        super().__init__(*args, **kwargs)

    def log_message(self, fmt, *args):
        log.debug("%s - %s", self.address_string(), fmt % args)

    def do_GET(self):
        url = urlparse(self.path)
        try:
            if url.path in ("/", "/index.html"):
                self._file(STATIC_DIR / "index.html")
            elif url.path.startswith("/static/"):
                self._static(url.path[len("/static/"):])
            elif url.path == "/api/readings":
                self._readings(parse_qs(url.query))
            elif url.path == "/api/latest":
                self._json(self._with_db(db.latest))
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def _with_db(self, fn, *args):
        conn = db.connect_readonly(self.db_path)
        try:
            return fn(conn, *args)
        finally:
            conn.close()

    def _readings(self, query):
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
        rows = self._with_db(db.fetch_readings, ts_from, ts_to)
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


def make_server(host: str, port: int, db_path: str) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), partial(Handler, db_path=db_path))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Aranet4 dashboard web server")
    parser.add_argument("--config", help="config.env path (default: ./config.env)")
    parser.add_argument("--db", help="SQLite path, overrides ARANET_DB")
    parser.add_argument("--host", help="bind address, overrides ARANET_HOST")
    parser.add_argument("--port", type=int, help="port, overrides ARANET_PORT")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    s = get_settings(args.config)
    server = make_server(args.host or s.host, args.port or s.port, args.db or s.db_path)
    log.info("dashboard on http://%s:%d (db %s)", *server.server_address[:2], args.db or s.db_path)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
