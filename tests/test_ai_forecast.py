import json
import sys
import threading
import urllib.error
import urllib.request

from aranet_monitor import ai_forecast, dashboard, weather


def obs(station, ts):
    row = {c: None for c in weather.VALUE_COLUMNS}
    row.update(station=station, ts=ts, temp=20.0, rh=60.0, rain=0.0, wind10=3.0, wdir=270.0, p_msl=1012.0, extra=None)
    return row


NOW = 1_791_300_000


def setup(tmp_path):
    path = str(tmp_path / "w.db")
    conn = weather.connect(path)
    rows = [obs("ATHALASSA", NOW - k * 600) for k in range(144)] + [obs("AGROS", NOW - k * 600) for k in range(144)]
    weather.store(conn, [("ATHALASSA", 35.14, 33.40), ("AGROS", 34.92, 33.02)], rows)
    conn.close()
    return path


def check_prompt(p):
    for head in ("## Наблюдения", "## Молнии", "## Метеорадар", "## ECMWF", "## Бюллетени", "## Предупреждения"):
        assert head in p
    lines = p.split("## Наблюдения")[1].split("## Молнии")[0].splitlines()
    key = [l for l in lines if l.startswith("ATHALASSA,")]
    other = [l for l in lines if l.startswith("AGROS,")]
    assert 23 <= len(key) <= 25 and 7 <= len(other) <= 9  # hourly vs every 3 h


def test_prompt_from_files_and_from_the_api_match(tmp_path, monkeypatch):
    path = setup(tmp_path)
    monkeypatch.setattr(ai_forecast, "ecmwf_csv", lambda src: "station,time\nATHALASSA,07.10 01:00")
    monkeypatch.setattr(ai_forecast, "radar_csv", lambda api, now: "радар не подключён")
    from_db = ai_forecast.build_prompt(ai_forecast.DbSource(path, str(tmp_path / "none.db")), NOW)
    check_prompt(from_db)
    srv = dashboard.make_server("127.0.0.1", 0, str(tmp_path / "a.db"), path, str(tmp_path / "none.db"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        from_api = ai_forecast.build_prompt(ai_forecast.ApiSource(f"http://127.0.0.1:{srv.server_address[1]}"), NOW)
    finally:
        srv.shutdown()
    assert from_api == from_db


def test_ask_save_and_serve(tmp_path):
    fake = tmp_path / "fake_llm.py"
    fake.write_text("import sys, json; sys.stdin.read(); print('thinking...'); print(json.dumps({'summary': 'ok'}))")
    answer, took = ai_forecast.ask("prompt", f"{sys.executable} {fake}")
    assert answer == {"summary": "ok"} and took >= 0
    ai_dir = str(tmp_path / "ai")
    ai_forecast.save(ai_dir, 100, "fake", answer, 6, took)
    assert ai_forecast.latest(ai_dir)["forecast"] == {"summary": "ok"}
    srv = dashboard.make_server("127.0.0.1", 0, str(tmp_path / "a.db"), str(tmp_path / "w.db"), ai_dir=ai_dir)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        got = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}/api/weather/ai-forecast").read())
        assert got["issued"] == 100 and got["model"] == "fake"
    finally:
        srv.shutdown()
    assert ai_forecast.latest(str(tmp_path / "nothing")) is None


def serve_json(payload):
    import http.server

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.server.paths.append(self.path)
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    srv.paths = []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_radar_half_hourly_frames_cells_and_empty_frames():
    frames = [{"ts": NOW - 3600 - k * 600, "sites": ["LCA"], "cells": [[35.0, 33.5, 100 + k, 0, 25.0]]} for k in range(3)]
    frames.append({"ts": NOW - 600, "sites": ["LCA", "PFO"], "cells": []})
    srv = serve_json({"available": True, "cell_degrees": 0.5, "frames": sorted(frames, key=lambda f: f["ts"])})
    try:
        text = ai_forecast.radar_csv(f"http://127.0.0.1:{srv.server_address[1]}", NOW)
    finally:
        srv.shutdown()
    assert srv.paths == [f"/api/weather/radar-cells?from={NOW - 3 * 3600}&to={NOW}"]
    rows = text.split("max_dbz\n")[1].splitlines()
    # the newest frame in each half hour back from NOW: -10' and -60' (which hides -70' and -80')
    assert len(rows) == 2 and rows[1].endswith("LCA+PFO,,,0,0,") and rows[0].endswith(",LCA,35.0,33.5,100,0,25.0")
    assert "молчит" not in text


def test_radar_missing_or_silent_is_said_so():
    assert ai_forecast.radar_csv(None, NOW) == "радар не подключён"
    assert ai_forecast.radar_csv("http://127.0.0.1:9", NOW) == "радарные данные недоступны"
    srv = serve_json({"available": True, "frames": []})
    try:
        assert "радарных кадров нет" in ai_forecast.radar_csv(f"http://127.0.0.1:{srv.server_address[1]}", NOW)
    finally:
        srv.shutdown()
    srv = serve_json({"available": True, "frames": [{"ts": NOW - 7200, "sites": ["PFO"], "cells": []}]})
    try:
        assert "радар молчит 120 мин" in ai_forecast.radar_csv(f"http://127.0.0.1:{srv.server_address[1]}", NOW)
    finally:
        srv.shutdown()


def test_schema_is_strict():
    def walk(s):
        if s.get("type") == "object":
            assert s["additionalProperties"] is False and set(s["required"]) == set(s["properties"])
            for v in s["properties"].values():
                walk(v)
        if s.get("type") == "array":
            walk(s["items"])
    walk(ai_forecast.ANSWER_SCHEMA)


def test_a_failing_optional_source_is_retried_then_left_out(tmp_path, monkeypatch):
    path = setup(tmp_path)
    monkeypatch.setattr(ai_forecast, "RETRY_DELAYS", (0, 0))
    calls = []

    def down(src):
        calls.append(1)
        raise urllib.error.HTTPError("https://api.open-meteo.com", 503, "Service Unavailable", {}, None)

    monkeypatch.setattr(ai_forecast, "ecmwf_csv", down)
    monkeypatch.setattr(ai_forecast, "radar_csv", lambda api, now: "радар не подключён")
    prompt = ai_forecast.build_prompt(ai_forecast.DbSource(path, str(tmp_path / "none.db")), NOW)
    assert len(calls) == 3 and "ECMWF: данные недоступны" in prompt
    check_prompt(prompt)


def test_optional_returns_on_a_later_success(monkeypatch):
    monkeypatch.setattr(ai_forecast, "RETRY_DELAYS", (0, 0))
    answers = iter([OSError("reset"), "ok"])

    def flaky():
        a = next(answers)
        if isinstance(a, Exception):
            raise a
        return a

    assert ai_forecast.optional("X", flaky) == "ok"
