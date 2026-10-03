from aranet_monitor import db


def r(ts, co2=600):
    return db.Reading(ts=ts, co2=co2, temperature=21.5, humidity=40.0, pressure=1012.3)


def test_insert_and_fetch(conn):
    assert db.insert_readings(conn, [r(300), r(100), r(200)]) == 3
    rows = db.fetch_readings(conn)
    assert [x["ts"] for x in rows] == [100, 200, 300]
    assert db.last_ts(conn) == 300


def test_jittered_duplicates_are_skipped(conn):
    db.insert_readings(conn, [r(1000), r(1300)], min_gap=150)
    # same points re-derived a couple of seconds off + one genuinely new point
    n = db.insert_readings(conn, [r(1001), r(1302), r(1600)], min_gap=150)
    assert n == 1
    assert [x["ts"] for x in db.fetch_readings(conn)] == [1000, 1300, 1600]


def test_fetch_range(conn):
    db.insert_readings(conn, [r(t) for t in range(0, 1000, 100)])
    assert [x["ts"] for x in db.fetch_readings(conn, 300, 500)] == [300, 400, 500]


def test_latest(conn):
    assert db.latest(conn) == {"reading": None, "status": None}
    db.insert_readings(conn, [r(10, 500), r(20, 900)])
    db.insert_status(conn, 25, "Aranet4 1A2B3", "v1.4.19", 87, 300)
    out = db.latest(conn)
    assert out["reading"]["co2"] == 900
    assert out["status"]["battery"] == 87
