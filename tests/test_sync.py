import sqlite3

from aranet_monitor import db, sync


def test_snapshot_is_a_self_contained_copy(conn, db_path, tmp_path):
    db.insert_readings(conn, [db.Reading(100, 700, 21.0, 40.0, 1010.0), db.Reading(400, 710, 21.1, 41.0, 1010.1)])
    dst = str(tmp_path / "snap.db")
    sync.snapshot(db_path, dst)
    copy = sqlite3.connect(dst)
    assert copy.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert copy.execute("SELECT co2 FROM readings ORDER BY ts").fetchall() == [(700,), (710,)]


def test_main_without_target_is_noop(monkeypatch, tmp_path):
    monkeypatch.delenv("ARANET_SYNC_TARGET", raising=False)
    assert sync.main(["--config", str(tmp_path / "none.env")]) == 0


def test_main_pushes_snapshot(monkeypatch, conn, db_path, tmp_path):
    cfg = tmp_path / "c.env"
    cfg.write_text(f"ARANET_DB={db_path}\nARANET_SYNC_TARGET=aranet@example.com:aranet.db\nARANET_SYNC_KEY=/k\n")
    for k in ("ARANET_DB", "ARANET_SYNC_TARGET", "ARANET_SYNC_KEY"):
        monkeypatch.delenv(k, raising=False)
    calls = []
    monkeypatch.setattr(sync.subprocess, "run", lambda cmd, check: calls.append(cmd))
    assert sync.main(["--config", str(cfg)]) == 0
    cmd = calls[0]
    assert cmd[0] == "rsync" and cmd[-1] == "aranet@example.com:aranet.db"
    assert cmd[-2].endswith("sync-snapshot.db") and "-i /k" in cmd[cmd.index("-e") + 1]


def test_main_reports_push_failure(monkeypatch, conn, db_path, tmp_path):
    cfg = tmp_path / "c.env"
    cfg.write_text(f"ARANET_DB={db_path}\nARANET_SYNC_TARGET=x:y\n")
    for k in ("ARANET_DB", "ARANET_SYNC_TARGET"):
        monkeypatch.delenv(k, raising=False)

    def boom(cmd, check):
        raise sync.subprocess.CalledProcessError(12, cmd)

    monkeypatch.setattr(sync.subprocess, "run", boom)
    monkeypatch.setattr(sync.time, "sleep", lambda s: None)  # the retries
    assert sync.main(["--config", str(cfg)]) == 1
