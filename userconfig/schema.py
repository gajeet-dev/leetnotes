"""The shape of config.json, with defaults and validation."""

from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

CONFIG_VERSION = 1


class AppConfig(BaseModel):
    """None means 'not set here': the value falls back to .env, then to the built-in default."""

    model_config = ConfigDict(extra="ignore")  # old or newer files still load

    version: int = CONFIG_VERSION
    output_dirs: list[str] | None = None
    leetcode_username: str | None = None
    time_zone: str | None = None
    review_before_writing: bool = True
    requests_per_second: float | None = Field(default=None, gt=0)
    requests_per_minute: float | None = Field(default=None, gt=0)
    problems_per_minute: float | None = Field(default=None, gt=0)
    default_provider: str | None = None
    providers: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @field_validator("output_dirs")
    @classmethod
    def clean_output_dirs(cls, folders: list[str] | None) -> list[str] | None:
        """Trim, drop blanks and duplicates (keeping the order). An empty list means not set."""
        if folders is None:
            return None
        cleaned = [folder.strip() for folder in folders if folder.strip()]
        return list(dict.fromkeys(cleaned)) or None

    @field_validator("leetcode_username", "default_provider")
    @classmethod
    def trim_text(cls, value: str | None) -> str | None:
        return (value or "").strip() or None

    @field_validator("time_zone")
    @classmethod
    def known_time_zone(cls, name: str | None) -> str | None:
        if name is None:
            return None
        try:
            ZoneInfo(name)
        except ZoneInfoNotFoundError, ValueError:
            raise ValueError(
                f"unknown time zone {name!r} (use a name like Asia/Kolkata)"
            ) from None
        return name
