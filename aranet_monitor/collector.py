"""Collector: connect to the Aranet4 over Bluetooth, pull the history logged
since the last stored point (so gaps from downtime are back-filled from the
device memory), plus the current reading, and store them in SQLite.

Run once (systemd timer / cron every 10 minutes) or with --loop.
"""

import argparse
import asyncio
import logging
import sys
import time
from dataclasses import dataclass, field

import aranet4
from aranet4.client import Aranet4, Param

from . import db
from .config import get_settings

log = logging.getLogger("aranet.collect")



def _num(value, cast=float):
    """aranet4 marks missing values with -1 or None."""
    if value is None or value == -1:
        return None
    return cast(value)


@dataclass
class DeviceData:
    name: str
    version: str
    battery: int | None
    interval: int
    current: db.Reading
    history: list[db.Reading] = field(default_factory=list)
    history_error: str | None = None


def history_start(total: int, last_point_ts: int, interval: int, since_ts: int) -> int:
    """1-based index of the first logged point newer than `since_ts`.
    Point i was logged at last_point_ts - (total - i) * interval."""
    newer = (last_point_ts - since_ts) // interval  # points strictly after since_ts (+-1, deduped later)
    return max(1, total - newer)


async def _read_device(address: str, since_ts: int | None) -> DeviceData:
    # aranet4.client.get_all_records() treats any "Aranet4 ..." name as an unknown
    # model and returns nothing (aranet4 2.6.0), so history is read with the
    # low-level calls over the same connection as the current reading.
    monitor = Aranet4(address=address)
    await monitor.connect()
    try:
        interval = await monitor.get_interval()
        ago = await monitor.get_seconds_since_update()
        if interval - ago < 10:  # a new point is about to be logged: wait so indexes don't shift mid-read
            await asyncio.sleep(interval - ago + 2)
            ago = await monitor.get_seconds_since_update()
        cur = await monitor.current_readings(details=True)
        name = await monitor.get_name()
        version = await monitor.get_version()
        last_point_ts = int(time.time()) - ago

        data = DeviceData(
            name=name, version=version, battery=_num(cur.battery, int), interval=interval,
            current=db.Reading(
                ts=last_point_ts, co2=_num(cur.co2, int), temperature=_num(cur.temperature),
                humidity=_num(cur.humidity), pressure=_num(cur.pressure),
            ),
        )

        try:
            total = await monitor.get_total_readings()
            # first run (empty DB): take the whole device memory
            start = 1 if since_ts is None else history_start(total, last_point_ts, interval, since_ts)
            if total and start <= total:
                cols = {}
                for key, param in (("co2", Param.CO2), ("temperature", Param.TEMPERATURE),
                                   ("humidity", Param.HUMIDITY), ("pressure", Param.PRESSURE)):
                    cols[key] = await monitor.get_records(param, log_size=total, start=start, end=total)
                for i in range(start, total + 1):
                    data.history.append(db.Reading(
                        ts=last_point_ts - (total - i) * interval,
                        co2=_num(cols["co2"][i - 1], int),
                        temperature=_num(cols["temperature"][i - 1]),
                        humidity=_num(cols["humidity"][i - 1]),
                        pressure=_num(cols["pressure"][i - 1]),
                    ))
        except Exception as exc:  # history is a separate GATT exchange that can fail on its own
            data.history_error = repr(exc)
        return data
    finally:
        try:
            await monitor.device.disconnect()
        except Exception:
            pass


def read_device(address: str, since_ts: int | None) -> DeviceData:
    return asyncio.run(_read_device(address, since_ts))


def collect_once(address: str, db_path: str) -> int:
    conn = db.connect(db_path)
    try:
        data = read_device(address, db.last_ts(conn))
        db.insert_status(conn, int(time.time()), data.name, data.version, data.battery, data.interval)
        if data.history_error:
            log.warning("history read failed, storing the current reading only: %s", data.history_error)
        # half an interval: the same point re-derived on a later run lands within a few seconds
        min_gap = data.interval // 2
        n = db.insert_readings(conn, data.history, min_gap=min_gap)
        n += db.insert_readings(conn, [data.current], min_gap=min_gap)
        c = data.current
        log.info(
            "stored %d new point(s) (%d read from history); now CO2=%s ppm T=%s C RH=%s%% P=%s hPa battery=%s%%",
            n, len(data.history), c.co2, c.temperature, c.humidity, c.pressure, data.battery,
        )
        return n
    finally:
        conn.close()


def collect_with_retry(address: str, db_path: str, attempts: int = 3, delay: int = 20) -> bool:
    for i in range(1, attempts + 1):
        try:
            collect_once(address, db_path)
            return True
        except Exception as exc:
            log.warning("attempt %d/%d failed: %s", i, attempts, exc)
            if i < attempts:
                time.sleep(delay)
    return False


def scan(duration: int = 10) -> None:
    found = {}

    def on_detect(adv):
        if adv.readings and adv.device.address not in found:
            found[adv.device.address] = adv
            print(f"{adv.device.address}  {adv.device.name}  RSSI {adv.rssi} dBm")

    print(f"Scanning for {duration}s...")
    devices = aranet4.client.find_nearby(on_detect, duration)
    for d in devices:
        if d.address not in found:
            print(f"{d.address}  {d.name}  (no readings in advertisement - enable Smart Home integrations in the app)")
    if not devices and not found:
        print("No Aranet devices found.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Collect Aranet4 readings into SQLite")
    parser.add_argument("--config", help="config.env path (default: ./config.env)")
    parser.add_argument("--address", help="device address, overrides ARANET_ADDRESS")
    parser.add_argument("--db", help="SQLite path, overrides ARANET_DB")
    parser.add_argument("--scan", action="store_true", help="list nearby Aranet devices and exit")
    parser.add_argument("--loop", action="store_true", help="run forever instead of once")
    parser.add_argument("--interval", type=int, default=600, help="seconds between runs with --loop")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.scan:
        scan()
        return 0

    settings = get_settings(args.config)
    address = args.address or settings.address
    db_path = args.db or settings.db_path
    if not address:
        print("No device address: set ARANET_ADDRESS in config.env or pass --address (see --scan)", file=sys.stderr)
        return 2

    if not args.loop:
        return 0 if collect_with_retry(address, db_path) else 1

    while True:
        started = time.monotonic()
        collect_with_retry(address, db_path)
        time.sleep(max(0, args.interval - (time.monotonic() - started)))


if __name__ == "__main__":
    sys.exit(main())
