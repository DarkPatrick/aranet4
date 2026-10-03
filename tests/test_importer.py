from datetime import datetime
from pathlib import Path

import pytest

from aranet_monitor import db, importer, weather

FIX = Path(__file__).parent / "fixtures"


def local(*a):
    return int(datetime(*a, tzinfo=weather.LOCAL_TZ).timestamp())


def test_parse_csv_summer_time():
    rows = importer.parse(str(FIX / "aranet_app_export.csv"))
    assert len(rows) == 3
    r = rows[0]
    assert r.ts == local(2026, 7, 5, 23, 35, 23)  # 11:35 PM, EEST
    assert (r.co2, r.temperature, r.humidity) == (873, 30.0, 69.0)
    assert r.pressure == pytest.approx(751 / 0.750062, abs=0.06)  # mmHg -> hPa


def test_parse_xlsx_winter_time():
    rows = importer.parse(str(FIX / "aranet_app_export.xlsx"))
    assert rows[0].ts == local(2026, 2, 22, 11, 22, 37)  # EET (+2)
    assert rows[0].co2 == 1097 and rows[0].pressure == pytest.approx(758 / 0.750062, abs=0.06)


def test_merge_keeps_existing_points(tmp_path):
    conn = db.connect(str(tmp_path / "a.db"))
    rows = importer.parse(str(FIX / "aranet_app_export.csv"))
    # the device memory already has the 2nd point, 40 s off and with a finer pressure
    db.insert_readings(conn, [db.Reading(rows[1].ts + 40, 850, 30.1, 69.0, 1001.3)])
    res = importer.merge(conn, rows, dry_run=True)
    assert (res["new"], res["already_there"]) == (2, 1)
    assert len(db.fetch_readings(conn)) == 1  # dry run wrote nothing
    importer.merge(conn, rows)
    got = db.fetch_readings(conn)
    assert len(got) == 3 and any(r["pressure"] == 1001.3 for r in got)
    assert importer.merge(conn, rows)["new"] == 0  # re-import is a no-op
