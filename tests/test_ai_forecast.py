import json
import sys
import threading
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
    for head in ("## Наблюдения", "## Молнии", "## ECMWF", "## Бюллетени", "## Предупреждения"):
        assert head in p
    lines = p.split("## Наблюдения")[1].split("## Молнии")[0].splitlines()
    key = [l for l in lines if l.startswith("ATHALASSA,")]
    other = [l for l in lines if l.startswith("AGROS,")]
    assert 23 <= len(key) <= 25 and 7 <= len(other) <= 9  # hourly vs every 3 h


def test_prompt_from_files_and_from_the_api_match(tmp_path, monkeypatch):
    path = setup(tmp_path)
    monkeypatch.setattr(ai_forecast, "ecmwf_csv", lambda src, place=None: "station,time\nATHALASSA,07.10 01:00")
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


def test_schema_is_strict():
    def walk(s):
        if s.get("type") == "object":
            assert s["additionalProperties"] is False and set(s["required"]) == set(s["properties"])
            for v in s["properties"].values():
                walk(v)
        if s.get("type") == "array":
            walk(s["items"])
    walk(ai_forecast.ANSWER_SCHEMA)
    walk(ai_forecast.make_schema(True))
    personal = ai_forecast.make_schema(True)["properties"]["horizons"]["items"]["properties"]["regions"]
    assert personal["items"]["properties"]["region"]["enum"][0] == ai_forecast.MY_PLACE and personal["minItems"] == 7


def test_location_post_and_personal_prompt(tmp_path, monkeypatch):
    path = setup(tmp_path)
    srv = dashboard.make_server("127.0.0.1", 0, str(tmp_path / "a.db"), path, str(tmp_path / "none.db"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    post = lambda body: json.loads(urllib.request.urlopen(urllib.request.Request(
        base + "/api/weather/location", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})).read())
    try:
        assert post({"lat": 48.85, "lon": 2.35})["stored"] is False  # Paris: not ours to forecast
        assert post({"lat": 34.95, "lon": 33.10})["stored"] is True
        got = json.loads(urllib.request.urlopen(base + "/api/weather/location").read())
        assert (got["lat"], got["lon"]) == (34.95, 33.1)
        seen = {}
        monkeypatch.setattr(ai_forecast, "ecmwf_csv", lambda src, place=None: seen.setdefault("place", place) and "МОЁ_МЕСТО,07.10 01:00")
        src = ai_forecast.ApiSource(base)
        p = ai_forecast.build_prompt(src, NOW, src.location())
    finally:
        srv.shutdown()
    assert "## Место пользователя" in p and "Твоё место" in p and seen["place"]["lat"] == 34.95
    # AGROS is the nearest station to that point: its observations go hourly now
    lines = p.split("## Наблюдения")[1].split("## Молнии")[0].splitlines()
    assert 23 <= len([l for l in lines if l.startswith("AGROS,")]) <= 25
