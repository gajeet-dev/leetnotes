"""Where the user's config and secrets live."""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR_ENV = "LEETNOTES_DATA_DIR"  # optional override, e.g. for tests


def data_dir() -> Path:
    override = os.environ.get(DATA_DIR_ENV)
    return Path(override).expanduser() if override else PROJECT_ROOT / "data"


def config_path() -> Path:
    return data_dir() / "config.json"


def secrets_path() -> Path:
    return data_dir() / "secrets.json"


def ensure_data_dir() -> Path:
    folder = data_dir()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    return folder


def worker_lock_path() -> Path:
    return data_dir() / "worker.lock"
