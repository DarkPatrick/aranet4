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
    status, ctype, body = get(server + "/")
    assert status == 200 and b"echarts.min.js" in body and ctype.startswith("text/html")
    status, _, body = get(server + "/static/echarts.min.js")
    assert status == 200 and len(body) > 100_000


def test_readings_window(server):
    _, _, body = get(server + "/api/readings?hours=1")
    data = json.loads(body)
    assert data["co2"] == [900]
    _, _, body = get(server + "/api/readings")
    assert len(json.loads(body)["ts"]) == 2


def test_latest(server):
    data = json.loads(get(server + "/api/latest")[2])
    assert data["reading"]["co2"] == 900 and data["status"]["battery"] == 77


@pytest.mark.parametrize("path,code", [
    ("/static/../db.py", 404),
    ("/nope", 404),
    ("/api/readings?hours=abc", 400),
    ("/api/readings?hours=-5", 400),
])
def test_errors(server, path, code):
    with pytest.raises(urllib.error.HTTPError) as exc:
        get(server + path)
    assert exc.value.code == code
