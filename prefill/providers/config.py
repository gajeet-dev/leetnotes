"""Reads the AI provider settings from the user-editable JSON file."""

import json
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


@dataclass(frozen=True)
class ProviderFile:
    default: str
    providers: dict[str, dict]


def config_file_path() -> Path:
    return Path(settings.PREFILL_PROVIDERS_FILE)


def read_json(path: Path) -> object:
    """Read and parse the file, turning every failure into a readable error."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ImproperlyConfigured(f"AI provider file not found: {path}") from None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ImproperlyConfigured(f"{path} is not valid JSON: {exc}") from exc


def check_shape(data: object, path: Path) -> ProviderFile:
    """Return the parsed file, or raise a readable error for anything unusable."""
    if not isinstance(data, dict) or not isinstance(data.get("providers"), dict):
        raise ImproperlyConfigured(f'{path} needs a top-level "providers" object')

    for name, entry in data["providers"].items():
        if not isinstance(entry, dict) or "type" not in entry:
            raise ImproperlyConfigured(f'{path}: provider "{name}" needs a "type"')

    default = data.get("default")
    if default not in data["providers"]:
        known = ", ".join(data["providers"])
        raise ImproperlyConfigured(f'{path}: "default" must be one of: {known}')

    return ProviderFile(default=default, providers=data["providers"])


def load_provider_file() -> ProviderFile:
    path = config_file_path()
    return check_shape(read_json(path), path)


def provider_entry(name: str | None = None) -> tuple[str, dict]:
    """Return (name, settings) for the named provider, or for the default one."""
    provider_file = load_provider_file()
    name = name or provider_file.default
    if name not in provider_file.providers:
        known = ", ".join(provider_file.providers)
        raise ImproperlyConfigured(f"Unknown AI provider {name!r}. Known: {known}")
    return name, provider_file.providers[name]
