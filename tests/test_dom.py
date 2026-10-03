import json
import threading
import urllib.request
from datetime import datetime
from pathlib import Path

import pytest

from aranet_monitor import dashboard, dom, weather

FIX = Path(__file__).parent / "fixtures"
TZ = weather.LOCAL_TZ


def local(y, m, d, h=0, mi=0):
    return int(datetime(y, m, d, h, mi, tzinfo=TZ).timestamp())


# ---------- NET ("feels like") ----------

@pytest.mark.parametrize("wind_kn,temp,rh,expected", [
    # rows of the Department of Meteorology's own AWS_NET.txt (2026-10-03 20:00)
    (2.7, 22.3, 72, 18.1),
    (0.8, 10.1, 87, 8.6),
    (3.4, 20.6, 80, 15.9),
    (1.7, 19.8, 73, 16.5),
])
def test_net_matches_published_values(wind_kn, temp, rh, expected):
    assert weather.net(temp, rh, wind_kn * weather.KNOT) == pytest.approx(expected, abs=0.1)


def test_net_missing_input():
    assert weather.net(20, None, 1) is None


# ---------- sea forecast ----------

def test_parse_sea_forecast():
    fc = dom.parse_sea((FIX / "sea_a_en.html").read_bytes(), "A")
    assert fc["valid_from"] == local(2026, 10, 3, 6) and fc["valid_to"] == local(2026, 10, 4, 6)
    assert fc["issued"] == local(2026, 10, 3, 5, 30)
    assert (fc["pressure"], fc["sst"], fc["warnings"]) == (1015, 27, "NIL")
    assert fc["visibility"].startswith("Good")
    assert fc["synopsis"].startswith("Low pressure and unstable airmass")
    areas = json.loads(fc["areas"])
    assert list(areas) == ["West Coast", "South Coast", "East Coast", "North Coast"]
    assert areas["West Coast"][0] == ["Morning", "Southeast to Southwest 3, soon South to Southwest 3 to 4", "Smooth to Slight"]
    assert all(len(v) == 3 for v in areas.values())


def test_until_2400_is_next_midnight():
    assert dom._local_ts("03/10/2026", "2400") == local(2026, 10, 4)


def test_store_sea_and_marine(tmp_path):
    conn = dom.connect(str(tmp_path / "w.db"))
    fc = dom.parse_sea((FIX / "sea_a_en.html").read_bytes(), "A")
    dom.store_sea(conn, fc)
    dom.store_sea(conn, dict(fc, sst=26.5))  # re-published: replaced, not duplicated
    m = dom.marine(conn)
    assert m["forecast"]["sst"] == 26.5 and m["forecast"]["areas"]["North Coast"][2][0] == "Night"
    assert m["sst"] == {"ts": [fc["valid_from"]], "sst": [26.5]}


# ---------- warnings ----------

def test_warnings_store_only_changes(tmp_path):
    conn = dom.connect(str(tmp_path / "w.db"))
    empty = b"<HTML>\n<H1>ISSUED WEATHER WARNINGS</H1>\n</HTML>\n"
    assert dom.parse_warnings(empty) == ""
    assert dom.store_warnings(conn, "", now=100)
    assert not dom.store_warnings(conn, "", now=200)
    yellow = dom.parse_warnings(b"<H1>ISSUED WEATHER WARNINGS</H1><p>YELLOW WARNING: thunderstorms</p>")
    assert yellow == "YELLOW WARNING: thunderstorms"
    assert dom.store_warnings(conn, yellow, now=300)
    assert dom.marine(conn)["warnings"] == {"ts": 300, "text": yellow}


# ---------- climate archive ----------

def test_parse_climate_tight_header_april_2021():
    rows = dom.parse_climate((FIX / "climate_2021_04.txt").read_text(encoding="utf-8"), 2021, 4)
    names = {r["name"] for r in rows}
    assert names == {"Pafos Airport", "Prodromos (CFC)", "Athalassa", "Larnaka Airport",
                     "New Limassol Port", "Paralimni (St. Frenaros)"}
    first = {r["name"]: r for r in rows if r["date"] == "2021-04-01"}
    assert (first["Pafos Airport"]["tmax"], first["Pafos Airport"]["tmin"], first["Pafos Airport"]["rain"]) == (16.3, 8.4, 16.0)
    assert (first["Prodromos (CFC)"]["tmax"], first["Prodromos (CFC)"]["tmin"]) == (5.1, -1.4)
    assert first["Paralimni (St. Frenaros)"]["rain"] == 0.3
    assert max(int(r["date"][-2:]) for r in rows) == 30


def test_parse_climate_blank_and_trace_cells():
    rows = dom.parse_climate((FIX / "climate_2023_12.txt").read_text(encoding="utf-8"), 2023, 12)
    by = {(r["name"], r["date"]): r for r in rows}
    d1 = by[("Pafos Airport", "2023-12-01")]
    assert (d1["tmax"], d1["tmin"], d1["rain"]) == (21.8, 12.1, None)  # blank rain cell stays blank
    assert by[("Prodromos (CFC)", "2023-12-01")]["rain"] == 0.0         # not shifted into the gap
    d10 = by[("Pafos Airport", "2023-12-10")]
    assert (d10["rain"], d10["trace"]) == (0.0, 1)


def test_station_codes_and_readings(tmp_path):
    conn = dom.connect(str(tmp_path / "w.db"))
    rows = dom.parse_climate((FIX / "climate_2021_04.txt").read_text(encoding="utf-8"), 2021, 4)
    dom.store_climate(conn, rows)
    codes = {s["code"] for s in dom.climate_stations(conn)}
    assert {"LCPH", "LCLK", "ATHALASSA", "LIMASSOL", "FRENAROS", "PRODROMOS"} == codes
    data = dom.climate_readings(conn, "LCPH", local(2021, 4, 1), local(2021, 4, 2, 12))
    assert data["ts"] == [local(2021, 4, 1), local(2021, 4, 2)] and data["tmax"][0] == 16.3


def test_pdf_month_names():
    assert dom.pdf_month("https://x/Tmax_Tmin_Precipitation_09_2026.pdf") == (2026, 9)
    assert dom.pdf_month("https://x/MAX%20-%20MIN%20-%20RAIN%20_12%20_2022.pdf") == (2022, 12)
    assert dom.pdf_month("https://x/readme.pdf") is None


# ---------- API ----------

def test_api_without_dom_tables(tmp_path):
    """the dashboard may open a weather.db the dom collector never touched"""
    path = str(tmp_path / "w.db")
    weather.connect(path).close()
    srv = dashboard.make_server("127.0.0.1", 0, str(tmp_path / "a.db"), path)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        assert json.loads(urllib.request.urlopen(base + "/api/weather/marine").read())["forecast"] is None
        assert json.loads(urllib.request.urlopen(base + "/api/weather/climate/stations").read()) == []
        assert json.loads(urllib.request.urlopen(base + "/api/weather/climate?station=LCPH").read())["ts"] == []
    finally:
        srv.shutdown()


def test_api_climate_and_net(tmp_path):
    path = str(tmp_path / "w.db")
    conn = dom.connect(path)
    dom.store_climate(conn, dom.parse_climate((FIX / "climate_2021_04.txt").read_text(encoding="utf-8"), 2021, 4))
    sample = (FIX / "cydom_sample.xml").read_bytes()
    weather.store(conn, *weather.parse(sample, now=local(2026, 10, 3, 20, 20)))
    conn.close()
    srv = dashboard.make_server("127.0.0.1", 0, str(tmp_path / "a.db"), path)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        data = json.loads(urllib.request.urlopen(base + f"/api/weather/climate?station=ATHALASSA&from={local(2021, 4, 1)}&to={local(2021, 4, 30)}").read())
        assert len(data["ts"]) == 30
        st = {s["code"]: s for s in json.loads(urllib.request.urlopen(base + "/api/weather/stations").read())}
        assert st["ACHNA"]["latest"]["net"] is not None and "net" in st["ACHNA"]["metrics"]
        rd = json.loads(urllib.request.urlopen(base + "/api/weather/readings?station=ACHNA").read())
        assert rd["net"] == [st["ACHNA"]["latest"]["net"]]
    finally:
        srv.shutdown()
