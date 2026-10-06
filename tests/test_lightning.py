import io
import json
import threading
import time
import urllib.request
import zipfile
from datetime import datetime, timezone

import h5py
import numpy as np

from aranet_monitor import dashboard, lightning


def make_nc(flashes) -> bytes:
    """A miniature LI-2-LFL body: [(unix ts, lat, lon, radiance)] packed like the real product."""
    buf = io.BytesIO()
    with h5py.File(buf, "w") as f:
        def put(name, values, dtype, scale=None, fill=None):
            ds = f.create_dataset(name, data=np.array(values, dtype=dtype))
            if scale is not None:
                ds.attrs["scale_factor"] = np.array([scale])
                ds.attrs["add_offset"] = np.array([0.0])
            if fill is not None:
                ds.attrs["_FillValue"] = np.array([fill], dtype=dtype)
        put("latitude", [round(la / 0.0027) for _, la, _, _ in flashes], "int16", 0.0027, -32767)
        put("longitude", [round(lo / 0.0027) for _, _, lo, _ in flashes], "int16", 0.0027, -32767)
        put("flash_time", [t - lightning.EPOCH for t, _, _, _ in flashes], "float64")
        put("radiance", [r for *_, r in flashes], "uint16", 1.0, 65535)
        put("flash_duration", [300] * len(flashes), "uint16")
        put("flash_footprint", [12] * len(flashes), "uint16")
    return buf.getvalue()


def zipped(nc: bytes) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("W_XX-EUMETSAT,LI-2-LFL--FD--CHK-TRAIL--x.nc", b"\x89HDF tiny trailer")
        z.writestr("W_XX-EUMETSAT,LI-2-LFL--FD--CHK-BODY--x.nc", nc)
        z.writestr("EOPMetadata.xml", "<x/>")
    return out.getvalue()


T0 = datetime(2026, 10, 6, 14, 40, tzinfo=timezone.utc).timestamp()
FLASHES = [(T0 + 12.345, 35.1, 33.4, 120), (T0 + 300, 36.3, 34.0, 65535),  # Cyprus; Turkish coast, no radiance
           (T0 + 10, 48.8, 2.3, 50)]  # Paris: outside the box


def test_parse_takes_the_body_and_keeps_the_box():
    rows = lightning.parse(zipped(make_nc(FLASHES)))
    assert [(round(r[0], 3), round(r[1], 2), round(r[2], 2), r[3]) for r in rows] == \
        [(round(T0 + 12.345, 3), 35.1, 33.4, 120.0), (T0 + 300, 36.3, 34.0, None)]
    assert rows[0][4:] == (300, 12)


def test_collect_resumes_after_the_last_window(tmp_path, monkeypatch):
    path = str(tmp_path / "l.db")
    w1 = {"id": "A", "start": datetime.fromtimestamp(T0, timezone.utc), "end": datetime.fromtimestamp(T0 + 600, timezone.utc), "url": "u1"}
    w2 = {"id": "B", "start": datetime.fromtimestamp(T0 + 600, timezone.utc), "end": datetime.fromtimestamp(T0 + 1200, timezone.utc), "url": "u2"}
    asked = []
    monkeypatch.setattr(lightning, "search", lambda since: asked.append(since) or [p for p in (w1, w2) if p["start"] >= since])
    monkeypatch.setattr(lightning, "token", lambda k, s: "tok")
    monkeypatch.setattr(lightning, "download", lambda url, bearer: zipped(make_nc(FLASHES if url == "u1" else [(T0 + 700, 35.0, 33.0, 10)])))
    monkeypatch.setattr(lightning, "CATCH_UP", lightning.timedelta(days=36500))
    assert lightning.collect(path, "k", "s") == 2
    assert lightning.collect(path, "k", "s") == 0  # nothing newer than B
    assert asked[1] > w2["start"]
    conn = lightning.connect(path)
    d = lightning.recent(conn, T0, T0 + 1200)
    assert len(d["ts"]) == 3 and d["window"]["end"] == int(T0 + 1200)


def test_push_target_sits_next_to_aranet_db(tmp_path, monkeypatch):
    from aranet_monitor import sync
    sent = []
    monkeypatch.setattr(sync, "push", lambda path, target, key: sent.append(target))
    path = str(tmp_path / "l.db")
    lightning.connect(path).close()
    lightning.push(path, "aranet@example.org:aranet.db", "key")
    assert sent == ["aranet@example.org:lightning.db"]


def test_api_lightning(tmp_path):
    path = str(tmp_path / "l.db")
    conn = lightning.connect(path)
    now = time.time()
    p = {"id": "A", "start": datetime.fromtimestamp(now - 700, timezone.utc), "end": datetime.fromtimestamp(now - 100, timezone.utc)}
    lightning.store(conn, p, [(now - 200, 35.0, 33.0, 5.0, 100, 3), (now - 7200, 35.0, 33.1, 5.0, 100, 3)])
    conn.close()
    srv = dashboard.make_server("127.0.0.1", 0, str(tmp_path / "a.db"), str(tmp_path / "w.db"), path)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        d = json.loads(urllib.request.urlopen(base + "/api/weather/lightning").read())
        assert len(d["ts"]) == 1 and d["window"]["end"] == int(now - 100)  # the last hour only
        srv2 = dashboard.make_server("127.0.0.1", 0, str(tmp_path / "a.db"), str(tmp_path / "w.db"), str(tmp_path / "none.db"))
        threading.Thread(target=srv2.serve_forever, daemon=True).start()
        empty = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{srv2.server_address[1]}/api/weather/lightning").read())
        assert empty["ts"] == [] and empty["window"] is None
        srv2.shutdown()
    finally:
        srv.shutdown()


def test_known_windows_are_not_fetched_again(tmp_path, monkeypatch):
    path = str(tmp_path / "l.db")
    w1 = {"id": "A", "start": datetime.fromtimestamp(T0, timezone.utc), "end": datetime.fromtimestamp(T0 + 600, timezone.utc), "url": "u1"}
    w2 = {"id": "B", "start": datetime.fromtimestamp(T0 + 600, timezone.utc), "end": datetime.fromtimestamp(T0 + 1200, timezone.utc), "url": "u2"}
    fetched = []
    # like the Data Store: everything overlapping `since`, the last stored window included
    monkeypatch.setattr(lightning, "search", lambda since: [p for p in (w1, w2) if p["end"] > since])
    monkeypatch.setattr(lightning, "token", lambda k, s: "tok")
    monkeypatch.setattr(lightning, "download", lambda url, bearer: fetched.append(url) or zipped(make_nc(FLASHES)))
    monkeypatch.setattr(lightning, "CATCH_UP", lightning.timedelta(days=36500))
    assert lightning.collect(path, "k", "s") == 2
    assert lightning.collect(path, "k", "s") == 0 and fetched == ["u1", "u2"]


def test_push_retries_when_the_server_is_busy(monkeypatch):
    import subprocess
    from aranet_monitor import sync
    calls = []

    def run(args, check):
        calls.append(args)
        if len(calls) < 3:
            raise subprocess.CalledProcessError(12, args)

    monkeypatch.setattr(sync.subprocess, "run", run)
    monkeypatch.setattr(sync.time, "sleep", lambda s: None)
    sync.push("db", "aranet@example.org:aranet.db", "key")
    assert len(calls) == 3
