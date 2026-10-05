"""Time aggregation of columnar series for the dashboard API.

Buckets are local (Cyprus) calendar units: hour, day, week (from Monday), month,
year; a bucket is stamped with its start. Values are the mean or the median of what
fell in, except sums (precipitation), maxima (running totals) and circular means
(wind direction). "auto" picks the step from the span the data actually covers, so
a station with a month of data under "all time" isn't squeezed into one point.
"""

import math
import statistics
from datetime import datetime, timedelta

from .weather import LOCAL_TZ

KINDS = ("raw", "hour", "day", "week", "month", "year")
STATS = ("mean", "median")
STEP = {"hour": 3600, "day": 86400, "week": 7 * 86400, "month": 30 * 86400, "year": 365 * 86400}
DAY = 86400


def auto_kind(span_s: float) -> str:
    """A couple of days as they are, then hours up to ~a month, days up to ~a year, ..."""
    if span_s <= 2 * DAY:
        return "raw"
    if span_s < 28 * DAY:
        return "hour"
    if span_s <= 400 * DAY:
        return "day"
    if span_s <= 3 * 366 * DAY:
        return "week"
    return "month"


def bucket(ts: int, kind: str) -> int:
    if kind == "hour":  # Cyprus is a whole number of hours off UTC
        return ts - ts % 3600
    d = datetime.fromtimestamp(ts, LOCAL_TZ)
    if kind == "day":
        start = datetime(d.year, d.month, d.day)
    elif kind == "week":
        start = datetime(d.year, d.month, d.day) - timedelta(days=d.weekday())
    elif kind == "month":
        start = datetime(d.year, d.month, 1)
    else:
        start = datetime(d.year, 1, 1)
    return int(start.replace(tzinfo=LOCAL_TZ).timestamp())


def _circular_mean(deg: list[float]) -> float:
    s = sum(math.sin(math.radians(v)) for v in deg)
    c = sum(math.cos(math.radians(v)) for v in deg)
    return round(math.degrees(math.atan2(s, c)), 1) % 360


def aggregate(data: dict, kind: str = "raw", stat: str = "mean",
              sums=(), maxes=(), circular=()) -> dict:
    """Columnar {"ts": [...], col: [...], other keys untouched} -> the same, bucketed.
    Adds "agg" (the resolved kind), "stat" and "step" (seconds, None for raw)."""
    if kind not in KINDS + ("auto",):
        raise ValueError(f"agg must be one of {', '.join(KINDS)}, auto")
    if stat not in STATS:
        raise ValueError(f"stat must be one of {', '.join(STATS)}")
    ts = data["ts"]
    if kind == "auto":
        kind = auto_kind(ts[-1] - ts[0] if ts else 0)
    if kind == "raw":
        return {**data, "agg": "raw", "stat": stat, "step": None}
    cols = [k for k, v in data.items() if k != "ts" and isinstance(v, list) and len(v) == len(ts) and ts]
    groups: dict[int, list[int]] = {}
    for i, t in enumerate(ts):
        groups.setdefault(bucket(t, kind), []).append(i)
    out = {**data, "agg": kind, "stat": stat, "step": STEP[kind], "ts": list(groups)}
    center = statistics.median if stat == "median" else statistics.fmean
    for c in cols:
        col, vals = data[c], []
        for idx in groups.values():
            v = [col[i] for i in idx if col[i] is not None]
            if not v:
                vals.append(None)
            elif c in sums:
                vals.append(round(sum(v), 2))
            elif c in maxes:
                vals.append(max(v))
            elif c in circular:
                vals.append(_circular_mean(v))
            else:
                vals.append(round(center(v), 2))
        out[c] = vals
    return out
