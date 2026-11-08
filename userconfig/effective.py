"""The settings the app actually uses: saved in data/config.json, with built-in defaults."""

from pathlib import Path

from .credentials import load_credentials
from .paths import PROJECT_ROOT
from .store import load_config

DEFAULT_OUTPUT_DIRS = [PROJECT_ROOT / "notes_output"]
DEFAULT_PROBLEMS_PER_MINUTE = 5.0
DEFAULT_REQUESTS_PER_SECOND = 1.0
DEFAULT_REQUESTS_PER_MINUTE = 20.0


def output_dirs() -> list[Path]:
    saved = load_config().output_dirs
    if saved is not None:
        return [Path(folder).expanduser() for folder in saved]
    return list(DEFAULT_OUTPUT_DIRS)


def create_directory() -> bool:
    return load_config().create_directory


def leetcode_username() -> str:
    return load_config().leetcode_username or ""


def requests_per_second() -> float:
    saved = load_config().requests_per_second
    return saved if saved is not None else DEFAULT_REQUESTS_PER_SECOND


def requests_per_minute() -> float:
    saved = load_config().requests_per_minute
    return saved if saved is not None else DEFAULT_REQUESTS_PER_MINUTE


def problems_per_minute() -> float:
    saved = load_config().problems_per_minute
    return saved if saved is not None else DEFAULT_PROBLEMS_PER_MINUTE


def leetcode_session() -> str:
    return load_credentials().session


def leetcode_csrf_token() -> str:
    return load_credentials().csrf_token
