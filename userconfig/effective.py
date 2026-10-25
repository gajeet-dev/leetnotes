"""The settings the app actually uses: saved settings first, .env as the fallback."""

from pathlib import Path

from django.conf import settings

from .credentials import load_credentials
from .store import load_config


def output_dirs() -> list[Path]:
    saved = load_config().output_dirs
    folders = saved if saved is not None else settings.NOTES_OUTPUT_DIRS
    return [Path(folder).expanduser() for folder in folders]


def leetcode_username() -> str:
    return load_config().leetcode_username or settings.LEETCODE_USERNAME


def requests_per_second() -> float:
    return load_config().requests_per_second or settings.LEETCODE_REQUESTS_PER_SECOND


def requests_per_minute() -> float:
    return load_config().requests_per_minute or settings.LEETCODE_REQUESTS_PER_MINUTE


def problems_per_minute() -> float:
    return load_config().problems_per_minute or settings.LEETCODE_PROBLEMS_PER_MINUTE


def leetcode_session() -> str:
    return load_credentials().session or settings.LEETCODE_SESSION


def leetcode_csrf_token() -> str:
    return load_credentials().csrf_token or settings.LEETCODE_CSRF_TOKEN
