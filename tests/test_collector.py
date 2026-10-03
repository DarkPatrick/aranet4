import time
from types import SimpleNamespace

import pytest
from aranet4.client import Param

from aranet_monitor import collector, db

INTERVAL = 300


class FakeMonitor:
    """Stands in for aranet4.client.Aranet4: `log` holds the device memory
    (oldest first) as dicts with co2/temperature/humidity/pressure."""

    log: list[dict] = []
    ago = 30
    fail_history = False

    def __init__(self, address):
        self.device = SimpleNamespace(disconnect=self._disconnect)

    async def _disconnect(self):
        pass

    async def connect(self):
        pass

    async def get_interval(self):
        return INTERVAL

    async def get_seconds_since_update(self):
        return FakeMonitor.ago

    async def current_readings(self, details=False):
        return SimpleNamespace(battery=91, **FakeMonitor.log[-1])

    async def get_name(self):
        return "Aranet4 TEST"

    async def get_version(self):
        return "v2.0.7"

    async def get_total_readings(self):
        return len(FakeMonitor.log)

    async def get_records(self, param, log_size, start=1, end=0xFFFF):
        if FakeMonitor.fail_history:
            raise RuntimeError("GATT error")
        key = {Param.CO2: "co2", Param.TEMPERATURE: "temperature",
               Param.HUMIDITY: "humidity", Param.PRESSURE: "pressure"}[param]
        out = [-1] * log_size
        for i in range(start, end + 1):
            out[i - 1] = FakeMonitor.log[i - 1][key]
        return out


def point(co2):
    return {"co2": co2, "temperature": 21.5, "humidity": 40, "pressure": 1010.0}


@pytest.fixture
def fake(monkeypatch):
    FakeMonitor.log = [point(600 + i) for i in range(10)]
    FakeMonitor.ago = 30
    FakeMonitor.fail_history = False
    monkeypatch.setattr(collector, "Aranet4", FakeMonitor)
    return FakeMonitor


def co2s(db_path):
    return [r["co2"] for r in db.fetch_readings(db.connect(db_path))]


def test_history_start():
    # last point at t=1000, interval 100: points at 100..1000 for total=10
    assert collector.history_start(10, 1000, 100, since_ts=700) == 7  # 700 (dup, filtered later), 800, 900, 1000
    assert collector.history_start(10, 1000, 100, since_ts=-10_000) == 1
    assert collector.history_start(10, 1000, 100, since_ts=1000) == 10


def test_first_run_backfills_device_memory(fake, db_path):
    assert collector.collect_once("AA:BB:CC:DD:EE:FF", db_path) == 10
    assert co2s(db_path) == list(range(600, 610))
    rows = db.fetch_readings(db.connect(db_path))
    assert rows[1]["ts"] - rows[0]["ts"] == INTERVAL
    assert abs(rows[-1]["ts"] - (time.time() - 30)) < 5
    assert db.latest(db.connect(db_path))["status"]["battery"] == 91


def test_next_run_adds_only_new_points(fake, db_path, monkeypatch):
    collector.collect_once("AA:BB:CC:DD:EE:FF", db_path)
    # 10 minutes later the device has logged two more points
    real_time = time.time
    monkeypatch.setattr(collector.time, "time", lambda: real_time() + 2 * INTERVAL + 3)
    fake.log.append(point(700))
    fake.log.append(point(701))
    assert collector.collect_once("AA:BB:CC:DD:EE:FF", db_path) == 2
    assert co2s(db_path)[-3:] == [609, 700, 701]


def test_history_failure_leaves_gap_for_next_run(fake, db_path, monkeypatch):
    collector.collect_once("AA:BB:CC:DD:EE:FF", db_path)
    real_time = time.time
    monkeypatch.setattr(collector.time, "time", lambda: real_time() + 3 * INTERVAL + 3)
    fake.log += [point(700), point(701), point(702)]
    fake.fail_history = True
    assert collector.collect_once("AA:BB:CC:DD:EE:FF", db_path) == 0  # current not stored past the gap
    fake.fail_history = False
    assert collector.collect_once("AA:BB:CC:DD:EE:FF", db_path) == 3
    assert co2s(db_path)[-4:] == [609, 700, 701, 702]


def test_missing_values_become_null(fake, db_path):
    fake.log = [point(-1)]
    collector.collect_once("AA:BB:CC:DD:EE:FF", db_path)
    assert co2s(db_path) == [None]


def test_main_requires_address(monkeypatch, tmp_path):
    monkeypatch.delenv("ARANET_ADDRESS", raising=False)
    assert collector.main(["--config", str(tmp_path / "none.env"), "--db", str(tmp_path / "x.db")]) == 2
