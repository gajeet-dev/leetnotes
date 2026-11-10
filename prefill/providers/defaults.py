"""The providers that ship with the app. Defined in ai_providers.json in the project root."""

import json
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
import structlog

from userconfig.paths import ai_providers_path

logger = structlog.get_logger(__name__)


def load_builtin_providers(path: Path | None = None) -> dict[str, dict]:
    """Load built-in providers from ai_providers.json (the single source of truth)."""
    target_path = path or ai_providers_path()
    if not target_path.is_file():
        raise ImproperlyConfigured(f"Missing AI providers file: {target_path}")

    try:
        data = json.loads(target_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ImproperlyConfigured(f"Invalid JSON in {target_path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ImproperlyConfigured(f"Root of {target_path} must be a JSON object")

    return {
        key: value
        for key, value in data.items()
        if not key.startswith("_") and isinstance(value, dict)
    }


BUILTIN_PROVIDERS: dict[str, dict] = load_builtin_providers()

DEFAULT_PROVIDER_NAME = "manual"
