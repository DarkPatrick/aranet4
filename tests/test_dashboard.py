import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from aranet_monitor import dashboard, db


@pytest.fixture
def server(db_path):
    now = int(time.time())
    conn = db.connect(db_path)
    db.insert_readings(conn, [
        db.Reading(now - 3 * 3600, 600, 21.0, 40.0, 1010.0),
        db.Reading(now - 600, 900, 22.0, 42.0, 1011.0),
    ])
    db.insert_status(conn, now, "Aranet4 TEST", "v1", 77, 300)
    conn.close()
    srv = dashboard.make_server("127.0.0.1", 0, db_path)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def get(url):
    with urllib.request.urlopen(url) as resp:
        return resp.status, resp.headers.get("Content-Type"), resp.read()


def test_index_and_static(server):
    status, ctype, body = get(server + "/weather/home")
    assert status == 200 and b"echarts.min.js" in body and ctype.startswith("text/html")
    status, _, body = get(server + "/static/echarts.min.js")
    assert status == 200 and len(body) > 100_000


def test_readings_window(server):
    _, _, body = get(server + "/api/readings?hours=1")
    data = json.loads(body)
    assert data["co2"] == [900]
    _, _, body = get(server + "/api/readings")
    assert len(json.loads(body)["ts"]) == 2


def test_readings_from_to(server):
    now = int(time.time())
    data = json.loads(get(server + f"/api/readings?from={now - 4 * 3600}&to={now - 3600}")[2])
    assert data["co2"] == [600]
    data = json.loads(get(server + f"/api/readings?from={now - 3600}")[2])
    assert data["co2"] == [900]


def test_latest(server):
    data = json.loads(get(server + "/api/latest")[2])
    assert data["reading"]["co2"] == 900 and data["status"]["battery"] == 77


@pytest.mark.parametrize("path,code", [
    ("/static/../db.py", 404),
    ("/nope", 404),
    ("/api/readings?hours=abc", 400),
    ("/api/readings?hours=-5", 400),
    ("/api/readings?from=200&to=100", 400),
    ("/api/readings?from=x", 400),
])
def test_errors(server, path, code):
    with pytest.raises(urllib.error.HTTPError) as exc:
        get(server + path)
    assert exc.value.code == code


def test_missing_db_reads_as_empty(tmp_path):
    path = tmp_path / "absent.db"
    srv = dashboard.make_server("127.0.0.1", 0, str(path))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        assert json.loads(get(base + "/api/readings")[2])["ts"] == []
        assert json.loads(get(base + "/api/latest")[2])["reading"] is None
        assert not path.exists()  # the dashboard never creates or writes the database
    finally:
        srv.shutdown()


@pytest.mark.parametrize("old,new", [("/", "/weather/home"), ("/weather", "/weather/outdoor"), ("/weather.html", "/weather/outdoor")])
def test_old_addresses_redirect(server, old, new):
    class NoFollow(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    opener = urllib.request.build_opener(NoFollow)
    with pytest.raises(urllib.error.HTTPError) as exc:
        opener.open(server + old)
    assert exc.value.code == 302 and exc.value.headers["Location"] == new
    assert get(server + old)[0] == 200  # a browser just follows it


def test_pressure_offset(tmp_path):
    from aranet_monitor import dom, weather
    now = int(time.time())
    a_path, w_path = str(tmp_path / "a.db"), str(tmp_path / "w.db")
    conn = db.connect(a_path)
    db.insert_readings(conn, [db.Reading(now - k * 300, 600, 21.0, 50.0, 1010.0) for k in range(24)])
    conn.close()
    assert dashboard.pressure_offset(a_path, w_path) == {"offset": None, "stations": 0}
    wx = dom.connect(w_path)
    rows = []
    for k in range(12):
        base = {c: None for c in weather.VALUE_COLUMNS}
        rows.append({**base, "station": "A", "ts": now - k * 600, "p_msl": 1014.0, "extra": None})
        rows.append({**base, "station": "B", "ts": now - k * 600, "p_qnh": 1015.0, "extra": None})
        rows.append({**base, "station": "C", "ts": now - k * 600, "p_station": 830.0, "extra": None})  # mountain: ignored
    weather.store(wx, [("A", 35, 33), ("B", 35, 33), ("C", 35, 33)], rows)
    wx.close()
    assert dashboard.pressure_offset(a_path, w_path) == {"offset": 4.5, "stations": 2}


def test_static_links_versioned_and_cached(tmp_path):
    import re
    import threading
    import urllib.error
    import urllib.request
    from aranet_monitor import dashboard
    srv = dashboard.make_server("127.0.0.1", 0, str(tmp_path / "a.db"), str(tmp_path / "w.db"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        page = urllib.request.urlopen(base + "/weather/outdoor")
        html = page.read().decode()
        links = re.findall(r'/static/[\w.-]+\.(?:js|css)\?v=\w+', html)
        assert any("weather.js?v=" in l for l in links) and any("echarts.min.js?v=" in l for l in links)
        js = urllib.request.urlopen(base + links[0])
        assert "immutable" in js.headers["Cache-Control"]
        # unversioned: revalidated, and an unchanged file answers 304
        plain = urllib.request.urlopen(base + "/static/common.js")
        assert plain.headers["Cache-Control"] == "no-cache"
        req = urllib.request.Request(base + "/static/common.js", headers={"If-None-Match": plain.headers["ETag"]})
        try:
            urllib.request.urlopen(req)
            assert False, "expected 304"
        except urllib.error.HTTPError as e:
            assert e.code == 304
        again = urllib.request.Request(base + "/weather/outdoor", headers={"If-None-Match": page.headers["ETag"]})
        try:
            urllib.request.urlopen(again)
            assert False, "expected 304"
        except urllib.error.HTTPError as e:
            assert e.code == 304
    finally:
        srv.shutdown()
