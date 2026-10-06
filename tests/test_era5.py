import urllib.error
from datetime import date, datetime, timedelta

from aranet_monitor import dom, era5, weather


def setup(tmp_path):
    path = str(tmp_path / "w.db")
    conn = dom.connect(path)
    weather.store(conn, [("ATHALASSA", 35.14, 33.40)], [])
    with conn:
        conn.executemany("INSERT INTO climate_stations (code, name) VALUES (?, ?)",
                         [("ATHALASSA", "Athalassa"), ("LIMASSOL_PUBLIC_GARDEN", "Limassol (Public Garden)"), ("NOWHERE", "?")])
        conn.executemany("INSERT INTO climate_daily (station, date, tmax, tmin) VALUES (?, ?, ?, ?)",
                         [("ATHALASSA", "2018-10-06", 33.5, 18.0), ("ATHALASSA", "2020-10-06", 31.0, 15.5),
                          ("ATHALASSA", "2020-10-07", 40.0, 5.0)])
    conn.close()
    return path


def test_fold_keeps_extremes_with_their_time(tmp_path):
    conn = era5.connect(setup(tmp_path))
    era5.fold(conn, "A", [("1990-10-06T14:00", 25.0), ("1990-10-06T03:00", 12.0), ("2010-10-06T14:00", 31.5)])
    era5.fold(conn, "A", [("2001-10-06T14:00", 20.0), ("2001-10-06T05:00", 9.5)])  # a later, colder year
    r = {(m, h): v for m, h, *v in conn.execute("SELECT md, hour, tmin, tmin_at, tmax, tmax_at FROM era5_records")}
    assert r[("10-06", 14)] == [20.0, "2001-10-06T14:00", 31.5, "2010-10-06T14:00"]
    assert r[("10-06", -1)] == [9.5, "2001-10-06T05:00", 31.5, "2010-10-06T14:00"]
    assert r[("10-06", 3)] == [12.0, "1990-10-06T03:00", 12.0, "1990-10-06T03:00"]


def test_collect_chunks_resumes_and_respects_the_budget(tmp_path, monkeypatch):
    path = setup(tmp_path)
    asked = []

    def fake_fetch(lat, lon, start, end):
        asked.append((round(lat, 2), start, end))
        last = min(end, date.today() - timedelta(days=5))  # ERA5-Land lags a few days
        if last < start:
            return []
        return [(f"{d.isoformat()}T{h:02d}:00", 20.0) for d in (start, last) for h in (0, 23)]

    monkeypatch.setattr(era5, "fetch", fake_fetch)
    monkeypatch.setattr(era5.time, "sleep", lambda s: None)
    n = era5.collect(path, budget=3 * 261)  # three ten-year requests
    assert n == 3 and asked[0][1] == date(1950, 1, 1) and asked[1][1] == date(1960, 1, 1)
    era5.collect(path, budget=1e9)
    conn = era5.connect(path)
    done = dict(conn.execute("SELECT station, done FROM era5_progress"))
    assert done["ATHALASSA"] == (date.today() - timedelta(days=5)).isoformat()
    assert "LIMASSOL_PUBLIC_GARDEN" in done and "NOWHERE" not in done  # no coordinates: skipped
    asked.clear()
    era5.collect(path, budget=1e9)  # nothing new yet: one small request per station
    assert len(asked) == 2 and all(s > date.today() - timedelta(days=6) for _, s, _ in asked)


def test_collect_stops_on_rate_limit(tmp_path, monkeypatch):
    path = setup(tmp_path)

    def limited(*a):
        raise urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None)

    monkeypatch.setattr(era5, "fetch", limited)
    assert era5.collect(path) == 0


def test_records_for_a_date_and_hour(tmp_path):
    conn = era5.connect(setup(tmp_path))
    era5.fold(conn, "ATHALASSA", [("1990-10-06T14:00", 25.0), ("2010-10-06T14:00", 31.5), ("1970-10-06T05:00", 8.0)])
    with conn:
        conn.execute("INSERT INTO era5_progress VALUES ('ATHALASSA', '2026-10-01')")
    r = era5.records(conn, "ATHALASSA", datetime(2026, 10, 6, 14, 30, tzinfo=weather.LOCAL_TZ))
    assert r["hourly"] == {"min": 25.0, "min_at": "1990-10-06T14:00", "max": 31.5, "max_at": "2010-10-06T14:00"}
    assert r["daily"]["min"] == 8.0 and r["period"] == ["1950-01-01", "2026-10-01"]
    assert r["archive"]["max"] == 33.5 and r["archive"]["max_at"] == "2018-10-06"
    assert r["archive"]["min"] == 15.5 and r["archive"]["years"] == 2  # 7 October doesn't count
    assert era5.records(conn, "NOWHERE")["hourly"] is None
