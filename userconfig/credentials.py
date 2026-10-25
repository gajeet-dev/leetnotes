"""The LeetCode cookies, kept in their own private file."""

import json
import os
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .errors import ConfigError
from .files import write_text_atomic
from .paths import ensure_data_dir, secrets_path
from .store import describe_validation_errors

logger = structlog.get_logger(__name__)

SESSION_COOKIE = "LEETCODE_SESSION"
CSRF_COOKIE = "csrftoken"


def clean_cookie_value(raw: str, cookie_name: str) -> str:
    """Tidy a cookie value pasted from the browser: just the value, nothing around it."""
    value = raw.strip()
    if value.lower().startswith(f"{cookie_name.lower()}="):
        value = value[len(cookie_name) + 1 :]
    value = value.strip().rstrip(";").strip().strip("\"'")
    if any(character.isspace() for character in value) or ";" in value:
        raise ValueError(
            "a cookie value has no spaces or semicolons: paste only that one value"
        )
    return value


class LeetCodeCredentials(BaseModel):
    # hide_input_in_errors keeps a bad cookie out of validation error messages.
    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)

    # repr=False keeps the values out of repr(), str() and log lines.
    session: str = Field(default="", repr=False)
    csrf_token: str = Field(default="", repr=False)
    updated_at: datetime | None = None

    @field_validator("session")
    @classmethod
    def tidy_session(cls, raw: str) -> str:
        return clean_cookie_value(raw, SESSION_COOKIE)

    @field_validator("csrf_token")
    @classmethod
    def tidy_csrf_token(cls, raw: str) -> str:
        return clean_cookie_value(raw, CSRF_COOKIE)

    @property
    def is_complete(self) -> bool:
        return bool(self.session and self.csrf_token)


# --- what the UI is allowed to see ---


@dataclass(frozen=True)
class CredentialsView:
    session_hint: str
    csrf_hint: str
    configured: bool
    updated_at: datetime | None


def mask_secret(value: str) -> str:
    """A hint that tells you something is stored, without revealing it."""
    if not value:
        return ""
    if len(value) < 16:
        return "•" * 8  # too short to show any of it safely
    return "•" * 8 + value[-4:]


def credentials_view(credentials: LeetCodeCredentials | None = None) -> CredentialsView:
    credentials = credentials or load_credentials()
    return CredentialsView(
        session_hint=mask_secret(credentials.session),
        csrf_hint=mask_secret(credentials.csrf_token),
        configured=credentials.is_complete,
        updated_at=credentials.updated_at,
    )


# --- loading ---


def tighten_permissions(path: Path) -> None:
    """Make the file owner-only if it isn't (for example, it was copied or restored)."""
    if os.name != "posix":
        return
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        path.chmod(0o600)
        logger.warning(
            "secrets_file_permissions_tightened", path=str(path), was=oct(mode)
        )


def load_credentials() -> LeetCodeCredentials:
    """The stored cookies, or an empty set if none were saved. Creates nothing."""
    path = secrets_path()
    if not path.exists():
        return LeetCodeCredentials()
    tighten_permissions(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise ConfigError(f"{path} is not valid JSON") from None
    try:
        return LeetCodeCredentials.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(
            f"{path} has invalid values:\n{describe_validation_errors(exc)}"
        ) from None


# --- changing ---


def save_credentials(credentials: LeetCodeCredentials) -> None:
    ensure_data_dir()
    text = credentials.model_dump_json(indent=2) + "\n"
    write_text_atomic(secrets_path(), text, mode=0o600)


def given_cookies(session: str | None, csrf_token: str | None) -> dict[str, str]:
    """Only the cookies that were actually filled in: a blank field means 'keep what's stored'."""
    given = {"session": session, "csrf_token": csrf_token}
    return {name: value for name, value in given.items() if value and value.strip()}


def update_credentials(
    *, session: str | None = None, csrf_token: str | None = None
) -> LeetCodeCredentials:
    """Replace the cookies you pass in. A missing or blank one keeps the stored value."""
    changes = given_cookies(session, csrf_token)
    current = load_credentials()
    if not changes:
        return current
    try:
        updated = LeetCodeCredentials.model_validate(
            {
                **current.model_dump(),
                **changes,
                "updated_at": datetime.now(timezone.utc),
            }
        )
    except ValidationError as exc:
        raise ConfigError(
            f"Invalid cookie:\n{describe_validation_errors(exc)}"
        ) from None
    save_credentials(updated)
    return updated


def clear_credentials() -> bool:
    """Delete the stored cookies. Returns whether there was anything to delete."""
    path = secrets_path()
    existed = path.exists()
    path.unlink(missing_ok=True)
    return existed
