from datetime import datetime, timezone

import pytest

from aranet_monitor import thermal, uv, weather

# reference values from pythermalcomfort.models.utci (the published polynomial)
@pytest.mark.parametrize("ta, tmrt, v, rh, expected", [
    (35, 35, 1, 60, 38.0), (25, 25, 5, 90, 22.9), (15, 15, 1, 30, 14.0), (30, 30, 1, 90, 35.5),
    (15, 15, 5, 30, 5.5),
])
def test_utci_matches_reference(ta, tmrt, v, rh, expected):
    assert thermal.utci(ta, tmrt, v, rh) == pytest.approx(expected, abs=0.05)


def test_categories():
    assert thermal.category(20) == "нет теплового стресса"
    assert thermal.category(33) == "сильный тепловой стресс"
    assert thermal.category(-5) == "умеренный холодовой стресс"


def test_sun_raises_feels_like_by_day_only():
    noon = datetime(2026, 7, 15, 10, tzinfo=timezone.utc).timestamp()  # 13:00 in Cyprus
    night = datetime(2026, 7, 15, 22, tzinfo=timezone.utc).timestamp()
    shade, sun = thermal.feels(35, 40, 2, 900, 35.14, 33.39, noon)
    assert 4 < sun - shade < 10
    assert 50 < thermal.tmrt_sun(35, 900, thermal.sun_elevation(noon, 35.14, 33.39), noon) < 70
    assert thermal.feels(25, 60, 2, 0, 35.14, 33.39, night) == (thermal.utci(25, 25, 2, 60),) * 2
    dni, dhi = thermal.split_radiation(900, 76, noon)
    assert dni > 600 and 100 < dhi < 300  # a clear day is mostly direct light


def obs(station, ts, rad=None, temp=30.0):
    row = {c: None for c in weather.VALUE_COLUMNS}
    row.update(station=station, ts=ts, temp=temp, rh=50.0, wind10=2.0, rad_global=rad, extra=None)
    return row


@pytest.fixture
def conn(tmp_path):
    weather._SITES.clear()
    c = uv.connect(str(tmp_path / "w.db"))
    now = datetime.now(timezone.utc)  # sensors are looked up in the last week: data has to be recent
    t = int(datetime(now.year, now.month, now.day, 9, tzinfo=timezone.utc).timestamp())  # noon in Cyprus
    stations = [("OWN", 34.70, 33.00), ("CITY", 34.72, 33.04), ("HILL", 34.73, 33.03), ("FAR", 35.30, 33.90)]
    rows = [obs("OWN", t + k * 600, rad=800.0 + k * 10) for k in range(-3, 4)]
    rows += [obs(s, t) for s in ("CITY", "HILL", "FAR")]
    weather.store(c, stations, rows)
    uv.store(c, [{"station": "OWN", "elevation": 10.0}, {"station": "CITY", "elevation": 40.0},
                 {"station": "HILL", "elevation": 900.0}, {"station": "FAR", "elevation": 20.0}])
    with c:
        c.executemany("INSERT INTO rad_model (station, ts, ghi) VALUES (?, ?, ?)",
                      [("FAR", t - 1800 + 1800, 500.0), ("FAR", t + 1800 + 1800, 700.0),
                       ("HILL", t + 1800, 650.0), ("HILL", t + 5400, 650.0)])
    return c, t


def test_radiation_source_own_near_model(conn):
    c, t = conn
    assert weather.radiation_source(c, "OWN")["kind"] == "own"
    near = weather.radiation_source(c, "CITY")
    assert near["kind"] == "near" and near["station"] == "OWN" and near["km"] < 15
    assert weather.radiation_source(c, "HILL")["kind"] == "model"  # close, but 890 m higher
    assert weather.radiation_source(c, "FAR")["kind"] == "model"   # too far


def test_radiation_series(conn):
    c, t = conn
    assert weather.radiation_series(c, {"kind": "own", "station": "OWN"}, [t]) == [800.0]
    # the neighbour is averaged over +-15 min: 790, 800, 810
    assert weather.radiation_series(c, {"kind": "near", "station": "OWN"}, [t]) == [800.0]
    # hourly model means stamped at the hour's end (t: 500, t+1h: 700) sit at the hours' middles
    # (t-30 min, t+30 min): t is halfway between them
    assert weather.radiation_series(c, {"kind": "model", "station": "FAR"}, [t, t + 900]) == [600.0, 650.0]


def test_readings_and_station_list_carry_utci(conn):
    c, t = conn
    d = weather.readings(c, "CITY")
    assert d["rad_src"]["kind"] == "near" and d["utci_sun"][0] > d["utci_shade"][0]
    latest = {s["code"]: s for s in weather.station_list(c)}
    assert latest["OWN"]["latest"]["utci_sun"] > latest["OWN"]["latest"]["utci_shade"]
    assert "utci_shade" in latest["OWN"]["metrics"]
