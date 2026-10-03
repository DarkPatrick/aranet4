"""Settings come from environment variables, optionally pre-loaded from a
`config.env` file (KEY=VALUE lines). Real environment variables win, so the
same file works both for systemd's EnvironmentFile and for local runs."""

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG_FILE = "config.env"


def load_env_file(path: str | os.PathLike) -> None:
    p = Path(path)
    if not p.is_file():
        return
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


@dataclass
class Settings:
    address: str
    db_path: str
    host: str
    port: int


def get_settings(config_file: str | None = None) -> Settings:
    load_env_file(config_file or os.environ.get("ARANET_CONFIG", DEFAULT_CONFIG_FILE))
    return Settings(
        address=os.environ.get("ARANET_ADDRESS", "").strip(),
        db_path=os.environ.get("ARANET_DB", "data/aranet.db"),
        host=os.environ.get("ARANET_HOST", "0.0.0.0"),
        port=int(os.environ.get("ARANET_PORT", "8080")),
    )
