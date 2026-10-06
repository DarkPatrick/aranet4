import json
import sys

from aranet_monitor import ai_forecast, weather


def obs(station, ts, **kw):
    row = {c: None for c in weather.VALUE_COLUMNS}
    row.update(station=station, ts=ts, temp=20.0, rh=60.0, rain=0.0, wind10=3.0, wdir=270.0, p_msl=1012.0, extra=None, **kw)
    return row


def test_prompt_sections_and_steps(tmp_path, monkeypatch):
    conn = ai_forecast.connect(str(tmp_path / "w.db"))
    now = 1_791_300_000
    rows = [obs("ATHALASSA", now - k * 600) for k in range(144)] + [obs("AGROS", now - k * 600) for k in range(144)]
    weather.store(conn, [("ATHALASSA", 35.14, 33.40), ("AGROS", 34.92, 33.02)], rows)
    monkeypatch.setattr(ai_forecast, "ecmwf_csv", lambda c: "station,time\nATHALASSA,07.10 01:00")
    p = ai_forecast.build_prompt(conn, None, now)
    for head in ("## Наблюдения", "## Молнии", "## ECMWF", "## Бюллетени", "## Предупреждения"):
        assert head in p
    lines = p.split("## Наблюдения")[1].split("## Молнии")[0].splitlines()
    key = [l for l in lines if l.startswith("ATHALASSA,")]
    other = [l for l in lines if l.startswith("AGROS,")]
    assert 23 <= len(key) <= 25 and 7 <= len(other) <= 9  # hourly vs every 3 h


def test_ask_reads_the_json_answer(tmp_path):
    fake = tmp_path / "fake_llm.py"
    fake.write_text("import sys, json; sys.stdin.read(); print('thinking...'); print(json.dumps({'summary': 'ok'}))")
    answer, took = ai_forecast.ask("prompt", f"{sys.executable} {fake}")
    assert answer == {"summary": "ok"} and took >= 0
    conn = ai_forecast.connect(str(tmp_path / "w.db"))
    ai_forecast.store(conn, 100, "fake", answer, 6, took)
    assert ai_forecast.latest(conn)["forecast"] == {"summary": "ok"}


def test_schema_is_strict():
    def walk(s):
        if s.get("type") == "object":
            assert s["additionalProperties"] is False and set(s["required"]) == set(s["properties"])
            for v in s["properties"].values():
                walk(v)
        if s.get("type") == "array":
            walk(s["items"])
    walk(ai_forecast.ANSWER_SCHEMA)
    json.dumps(ai_forecast.ANSWER_SCHEMA)
