import json
import threading
import urllib.request

from aranet_monitor import dashboard, uv, weather


def fake_response(stations):
    return [{"latitude": lat, "longitude": lon, "hourly": {
        "time": [1000, 4600, 8200], "uv_index": [0.0, 3.5, None], "uv_index_clear_sky": [0.0, 4.0, 5.0]}}
        for _, lat, lon in stations]


def test_collect_and_read(tmp_path, monkeypatch):
    path = str(tmp_path / "w.db")
    conn = weather.connect(path)
    weather.store(conn, [("A", 34.7, 33.0), ("B", 35.1, 33.4)], [])
    conn.close()
    calls = []

    def fake_fetch(stations, past_days=1, forecast_days=3):
        calls.append([s[0] for s in stations])
        return [{"station": code, "ts": t, "uv": u, "uv_clear": c, "fetched": 1}
                for (code, _, _), loc in zip(stations, fake_response(stations))
                for t, u, c in zip(loc["hourly"]["time"], loc["hourly"]["uv_index"], loc["hourly"]["uv_index_clear_sky"])]

    monkeypatch.setattr(uv, "fetch", fake_fetch)
    assert uv.collect(path) == 6
    assert uv.collect(path) == 6  # refresh overwrites, no duplicates
    assert calls == [["A", "B"], ["A", "B"]]
    data = uv.readings(uv.connect(path), "B", 2000)
    assert data == {"ts": [4600, 8200], "uv": [3.5, None], "uv_clear": [4.0, 5.0]}


def test_fetch_parses_multi_location(monkeypatch):
    stations = [("A", 34.7, 33.0), ("B", 35.1, 33.4)]

    class Resp:
        def __init__(self, body): self.body = body
        def read(self): return self.body
        def __enter__(self): return self
        def __exit__(self, *a): pass

    seen = {}
    def fake_urlopen(req, timeout=60):
        seen["url"] = req.full_url
        return Resp(json.dumps(fake_response(stations)).encode())

    monkeypatch.setattr(uv.urllib.request, "urlopen", fake_urlopen)
    rows = uv.fetch(stations)
    assert "latitude=34.7000%2C35.1000" in seen["url"] and "uv_index_clear_sky" in seen["url"]
    assert {(r["station"], r["ts"]) for r in rows} == {(s, t) for s in "AB" for t in (1000, 4600, 8200)}


def test_api_uv(tmp_path):
    path = str(tmp_path / "w.db")
    conn = uv.connect(path)
    uv.store(conn, [{"station": "A", "ts": 3600, "uv": 2.0, "uv_clear": 2.5, "fetched": 1}])
    conn.close()
    srv = dashboard.make_server("127.0.0.1", 0, str(tmp_path / "a.db"), path)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        assert json.loads(urllib.request.urlopen(base + "/api/weather/uv?station=A").read())["uv"] == [2.0]
        assert json.loads(urllib.request.urlopen(base + "/api/weather/uv?station=Z").read())["ts"] == []
    finally:
        srv.shutdown()
