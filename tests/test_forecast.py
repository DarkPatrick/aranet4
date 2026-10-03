import json
import threading
import urllib.request
from datetime import datetime
from pathlib import Path

from aranet_monitor import dashboard, forecast, weather

FIX = Path(__file__).parent / "fixtures"


def local(y, m, d, h=0, mi=0):
    return int(datetime(y, m, d, h, mi, tzinfo=weather.LOCAL_TZ).timestamp())


def test_parse_bulletin_c():
    b = forecast.parse_bulletin((FIX / "forecast_public_c.html").read_bytes(), "C")
    assert b["issued"] == local(2026, 10, 3, 16)
    assert (b["valid_from"], b["valid_to"]) == (local(2026, 10, 3, 18), local(2026, 10, 5))
    assert b["outlook"] == "(ΜΕ ΕΠΕΚΤΑΣΗ ΓΙΑ 3 ΗΜΕΡΕΣ)"
    assert b["paragraphs"][0] == "Ασταθής αέρια μάζα επηρεάζει την περιοχή."
    assert len(b["paragraphs"]) == 7 and not any("Θερμοκρασία" == p for p in b["paragraphs"])
    assert b["observed"][0] == ["Λευκωσία", 27.0, 13.0, 46.0]
    assert len(b["observed"]) == 7


def test_parse_bulletin_a_has_no_observations():
    b = forecast.parse_bulletin((FIX / "forecast_public_a.html").read_bytes(), "A")
    assert b["issued"] == local(2026, 10, 3, 5) and b["observed"] == [] and len(b["paragraphs"]) == 6


def test_sentences_and_cache(tmp_path):
    conn = forecast.connect(str(tmp_path / "w.db"))
    calls = []

    def fake(text, target="ru"):
        calls.append(text)
        return f"RU({text}) штормы"

    providers = [("fake", fake)]
    t, prov = forecast.translate_sentence(conn, "Α. ", providers)
    assert (t, prov) == ("RU(Α. ) грозы", "fake")  # glossary applied
    forecast.translate_sentence(conn, "Α. ", providers)
    assert len(calls) == 1  # second time from the cache
    assert forecast.sentences("Πρώτη. Δεύτερη; Τρίτη") == ["Πρώτη.", "Δεύτερη;", "Τρίτη"]


def test_failed_providers_leave_text_untranslated(tmp_path):
    conn = forecast.connect(str(tmp_path / "w.db"))

    def boom(text, target="ru"):
        raise RuntimeError("quota")

    assert forecast.translate_sentence(conn, "Κάτι.", [("x", boom)]) == (None, None)


def test_latest_and_api(tmp_path, monkeypatch):
    path = str(tmp_path / "w.db")
    conn = forecast.connect(path)
    b = forecast.parse_bulletin((FIX / "forecast_public_c.html").read_bytes(), "C")
    assert forecast.store_bulletin(conn, b) and not forecast.store_bulletin(conn, b)
    monkeypatch.setattr(forecast, "PROVIDERS", [("fake", lambda t, target="ru": "перевод")])
    forecast.translate_paragraph(conn, b["paragraphs"][0])
    conn.close()
    srv = dashboard.make_server("127.0.0.1", 0, str(tmp_path / "a.db"), path)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        d = json.loads(urllib.request.urlopen(base + "/api/weather/forecast").read())
        c = d["bulletins"][0]
        assert c["issue"] == "C" and c["providers"] == ["fake"]
        assert c["paragraphs"][0] == {"el": b["paragraphs"][0], "ru": "перевод"}
        assert c["paragraphs"][1]["ru"] is None  # not translated yet: page shows the Greek
        assert c["observed"][0]["place"] == "Никосия"
        assert c["table_image"].endswith("table_c_en.png")
        assert urllib.request.urlopen(base + "/weather/outdoor/forecast").status == 200
    finally:
        srv.shutdown()


def test_api_forecast_without_tables(tmp_path):
    srv = dashboard.make_server("127.0.0.1", 0, str(tmp_path / "a.db"), str(tmp_path / "none.db"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        d = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}/api/weather/forecast").read())
        assert d["bulletins"] == []
    finally:
        srv.shutdown()
