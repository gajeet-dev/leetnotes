"""Loading and saving config.json."""

import json
from pathlib import Path

from pydantic import ValidationError

from .errors import ConfigError
from .files import write_text_atomic
from .paths import config_path, ensure_data_dir
from .schema import AppConfig


def describe_validation_errors(exc: ValidationError, limit: int = 10) -> str:
    lines = [
        f"{'.'.join(str(part) for part in err['loc']) or '(root)'}: {err['msg']}"
        for err in exc.errors()
    ]
    if len(lines) > limit:
        lines = lines[:limit] + [f"... and {len(lines) - limit} more"]
    return "\n".join(lines)


def parse_config(text: str, source: Path) -> AppConfig:
    """Turn the file's text into settings, or raise a ConfigError that says what's wrong."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{source} is not valid JSON: {exc}") from exc
    try:
        return AppConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(
            f"{source} has invalid settings:\n{describe_validation_errors(exc)}"
        ) from exc


def load_config() -> AppConfig:
    """The saved settings, or the defaults if nothing has been saved yet. Creates nothing."""
    path = config_path()
    if not path.exists():
        return AppConfig()
    return parse_config(path.read_text(encoding="utf-8"), path)


def save_config(config: AppConfig) -> None:
    ensure_data_dir()
    write_text_atomic(config_path(), config.model_dump_json(indent=2) + "\n")


def update_config(changes: dict) -> AppConfig:
    """Change some settings, validate the result, and save it. Nothing is saved if it's invalid."""
    current = load_config()
    try:
        updated = AppConfig.model_validate({**current.model_dump(), **changes})
    except ValidationError as exc:
        raise ConfigError(
            f"Invalid settings:\n{describe_validation_errors(exc)}"
        ) from exc
    save_config(updated)
    return updated
