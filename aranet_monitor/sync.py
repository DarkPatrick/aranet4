"""Push a consistent copy of the database to a server (rsync over ssh).

The copy is made with SQLite's backup API in rollback-journal mode, so the
server gets a single self-contained file; rsync writes it to a temp name and
renames it, so a dashboard reading on the server never sees a half-written file.
"""

import argparse
import logging
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from .config import get_settings

log = logging.getLogger("aranet.sync")


def snapshot(src: str, dst: str) -> None:
    tmp = dst + ".tmp"
    Path(tmp).unlink(missing_ok=True)
    source = sqlite3.connect(src, timeout=30)
    target = sqlite3.connect(tmp)
    try:
        source.backup(target)
        target.execute("PRAGMA journal_mode=DELETE")
    finally:
        target.close()
        source.close()
    os.replace(tmp, dst)


def push(path: str, target: str, key: str) -> None:
    ssh = f"ssh -i {key} -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15"
    subprocess.run(["rsync", "-t", "--timeout=60", "-e", ssh, path, target], check=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Push a copy of the Aranet database to a server")
    parser.add_argument("--config", help="config.env path (default: ./config.env)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    s = get_settings(args.config)
    if not s.sync_target:
        log.info("ARANET_SYNC_TARGET is empty, nothing to do")
        return 0
    if not Path(s.db_path).exists():
        log.warning("no database at %s yet", s.db_path)
        return 0

    snap = str(Path(s.db_path).with_name("sync-snapshot.db"))
    snapshot(s.db_path, snap)
    try:
        push(snap, s.sync_target, s.sync_key)
    except (subprocess.CalledProcessError, OSError) as exc:
        log.error("push to %s failed: %s", s.sync_target, exc)
        return 1
    log.info("pushed %s -> %s", s.db_path, s.sync_target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
