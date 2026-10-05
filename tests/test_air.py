import json
import threading
import urllib.request
from datetime import datetime

from aranet_monitor import air, dashboard, weather

DLI_SAMPLE = {"data": {
    "2026100423": {"date_time": "2026-10-04 23:00:00", "pollutant_6001": "4.94880600", "pollutant_5": "14.91550000",
                   "pollutant_7": "28.45915000", "pollutant_8": "5.77480500", "pollutant_1": "1.34936900",
                   "pollutant_9": "5.97", "pollutant_38": "0.12"},
    "2026100500": {"date_time": "2026-10-05 00:00:00", "pollutant_7": "30.0", "pollutant_5": ""},
    "2026100501": {"date_time": "2026-10-05 01:00:00", "pollutant_9": "1.0"},  # nothing we keep
}}


def test_parse_dli_local_time_and_fields():
    rows = air.parse_dli("KALIND", DLI_SAMPLE)
    assert len(rows) == 2
    r = rows[0]
    assert r["ts"] == int(datetime(2026, 10, 4, 23, tzinfo=weather.LOCAL_TZ).timestamp())  # EEST, +03:00
    assert (r["pm25"], r["pm10"], r["o3"], r["no2"], r["so2"], r["co"]) == (4.95, 14.92, 28.46, 5.77, 1.35, None)
    assert rows[1]["pm10"] is None and rows[1]["o3"] == 30.0


def setup_db(path):
    conn = air.connect(path)
    # A sits in Nicosia, B on the south coast next to Zygi
    weather.store(conn, [("A", 35.125, 33.33), ("B", 34.73, 33.33)], [])
    return conn


def test_readings_pick_the_nearest_station_per_pollutant(tmp_path):
    conn = setup_db(str(tmp_path / "w.db"))
    air.store_obs(conn, [
        {"station": "NICTRA", "ts": 3600, "pm25": 9.0, "pm10": 20.0, "no2": 30.0, "o3": None, "so2": 1.0, "co": None},
        {"station": "NICRES", "ts": 3600, "pm25": None, "pm10": None, "no2": 10.0, "o3": 60.0, "so2": 1.0, "co": None},
        {"station": "ZYGIND", "ts": 3600, "pm25": 5.0, "pm10": 15.0, "no2": 4.0, "o3": 70.0, "so2": 2.0, "co": None},
    ])
    air.store_model(conn, [{"station": "A", "ts": t, "pm25": 6.0, "pm10": 8.0, "no2": 7.0, "o3": 50.0, "so2": 1.0,
                            "dust": 1.0, "eaqi": 20, "fetched": 1} for t in (3600, 7200)])
    d = air.readings(conn, "A")
    assert d["ts"] == [3600, 7200]
    # NICRES is the nearest but measures no PM: PM comes from the next one, NICTRA
    assert d["sources"]["no2"]["code"] == "NICRES" and d["sources"]["o3"]["code"] == "NICRES"
    assert d["sources"]["pm25"]["code"] == "NICTRA"
    assert d["pm25"] == [9.0, None] and d["pm25_cams"] == [6.0, 6.0] and d["eaqi_cams"] == [20, 20]
    b = air.readings(conn, "B")
    assert {s["code"] for s in b["sources"].values()} == {"ZYGIND"} and b["pm25_cams"] == [None]
    # a period without measurements: no sources, empty columns
    assert air.readings(conn, "A", 10000, 20000) == {"ts": [], "sources": {}, **{f"{c}_cams": [] for c in
           ["pm25", "pm10", "no2", "o3", "so2", "dust", "eaqi"]}, **{p: [] for p in air.POLLUTANTS}}


def test_collect_backfills_then_refreshes(tmp_path, monkeypatch):
    path = str(tmp_path / "w.db")
    setup_db(path).close()
    model_calls, dli_calls = [], []

    def fake_model(stations, past_days=1, forecast_days=4):
        model_calls.append(past_days)
        return [{"station": s[0], "ts": 3600, "pm25": 1.0, "pm10": 2.0, "no2": 3.0, "o3": 4.0, "so2": 5.0,
                 "dust": 0.0, "eaqi": 10, "fetched": 1} for s in stations]

    def fake_dli(sid, code, start, end):
        dli_calls.append((code, start, end))
        if code == "PARTRA":
            raise OSError("500 Service unavailable")  # an offline station doesn't stop the rest
        return [{"station": code, "ts": int(end.timestamp()) // 3600 * 3600, "pm25": 1.0, "pm10": None,
                 "no2": None, "o3": None, "so2": None, "co": None}]

    monkeypatch.setattr(air.time, "sleep", lambda s: None)
    monkeypatch.setattr(air, "fetch_model", fake_model)
    monkeypatch.setattr(air, "fetch_dli", fake_dli)
    obs, model = air.collect(path)
    assert model == 2 and model_calls == [30]
    assert obs == len(air.STATIONS) - 1
    assert all((e - s).days <= air.DLI_CHUNK_DAYS for _, s, e in dli_calls)
    dli_calls.clear()
    air.collect(path)
    assert model_calls == [30, 1]
    # the second run starts a few hours before each station's last value, one request each
    assert len(dli_calls) == len(air.STATIONS)


def test_fetch_model_multi_location(monkeypatch):
    stations = [("A", 35.1, 33.4), ("B", 34.7, 33.0)]
    hourly = {"time": [3600, 7200], **{v: [1.0, None] for v in air.CAMS_VARS.values()}}
    body = json.dumps([{"hourly": hourly}, {"hourly": hourly}]).encode()

    class Resp:
        def read(self): return body
        def __enter__(self): return self
        def __exit__(self, *a): pass

    seen = {}
    monkeypatch.setattr(air.urllib.request, "urlopen", lambda req, timeout=60: seen.setdefault("url", req.full_url) and Resp())
    rows = air.fetch_model(stations)
    assert "european_aqi" in seen["url"] and "latitude=35.1000%2C34.7000" in seen["url"]
    assert [(r["station"], r["ts"]) for r in rows] == [("A", 3600), ("B", 3600)]  # all-null hours dropped


def test_api_air(tmp_path):
    path = str(tmp_path / "w.db")
    conn = setup_db(path)
    air.store_obs(conn, [{"station": "NICTRA", "ts": 3600, "pm25": 9.0, "pm10": None, "no2": None, "o3": None,
                          "so2": None, "co": None}])
    conn.close()
    srv = dashboard.make_server("127.0.0.1", 0, str(tmp_path / "a.db"), path)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        d = json.loads(urllib.request.urlopen(base + "/api/weather/air?station=A").read())
        assert d["pm25"] == [9.0] and d["sources"]["pm25"]["kind"] == "транспортная"
        empty = json.loads(urllib.request.urlopen(base + "/api/weather/air?station=Z").read())
        assert empty["ts"] == [] and empty["sources"] == {}
    finally:
        srv.shutdown()


def test_backfill_goes_back_resumes_and_stops(tmp_path, monkeypatch):
    conn = air.connect(str(tmp_path / "w.db"))
    sleeps, calls = [], []
    monkeypatch.setattr(air.time, "sleep", sleeps.append)
    monkeypatch.setattr(air, "STATIONS", air.STATIONS[:2])
    born = datetime(2025, 1, 1)

    def fake_dli(sid, code, start, end):
        calls.append((code, start, end))
        if code == "NICRES" and len([c for c in calls if c[0] == code]) == 1:
            raise OSError("500")  # one transient failure: retried after a pause
        if end <= born:
            return []  # the station didn't exist yet
        return [{"station": code, "ts": int(end.timestamp()) - 3600, "pm25": 1.0, "pm10": None, "no2": None,
                 "o3": None, "so2": None, "co": None}]

    monkeypatch.setattr(air, "fetch_dli", fake_dli)
    air.store_obs(conn, [{"station": "NICTRA", "ts": int(datetime(2025, 9, 1, tzinfo=weather.LOCAL_TZ).timestamp()), "pm25": 1.0,
                          "pm10": None, "no2": None, "o3": None, "so2": None, "co": None}])
    air.backfill_obs(conn, datetime(2016, 1, 1), pause=10)
    tra = [c for c in calls if c[0] == "NICTRA"]
    assert tra[0][2] == datetime(2025, 9, 1)  # starts at the earliest stored value (local time)
    assert all(a[1] == b[2] for a, b in zip(tra, tra[1:]))  # contiguous, going back
    assert tra[-1][2] < born and tra[-1][1] > datetime(2023, 1, 1)  # a year of nothing: stop, not 2016
    assert 10 in sleeps and 60 in sleeps  # crawl delay + backoff
    res = [c for c in calls if c[0] == "NICRES"]
    assert res[0] == res[1]  # the failed chunk was asked again
    monkeypatch.setattr(air, "fetch_dli", lambda *a: (_ for _ in ()).throw(
        air.urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None)))
    try:
        air.backfill_obs(conn, datetime(2016, 1, 1))
        assert False, "should stop"
    except air.Blocked:
        pass


def test_first_ts(tmp_path):
    conn = setup_db(str(tmp_path / "w.db"))
    assert air.first_ts(conn) is None
    air.store_obs(conn, [{"station": "NICRES", "ts": 7200, "pm25": None, "pm10": None, "no2": 1.0, "o3": None,
                          "so2": None, "co": None}])
    assert air.first_ts(conn) == 7200
