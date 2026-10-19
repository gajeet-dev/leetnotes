"""Builds the right Provider from an entry in ai_providers.json."""

from django.core.exceptions import ImproperlyConfigured

from .base import Provider
from .cli import CliProvider
from .config import provider_entry

# The only place that knows which "type" maps to which class.
PROVIDER_TYPES: dict[str, type[Provider]] = {
    "cli": CliProvider,
}


def provider_class(type_name: str, name: str) -> type[Provider]:
    try:
        return PROVIDER_TYPES[type_name]
    except KeyError:
        known = ", ".join(PROVIDER_TYPES)
        raise ImproperlyConfigured(
            f'provider "{name}": unknown type {type_name!r}. Known types: {known}'
        ) from None


def build_provider(name: str, entry: dict) -> Provider:
    provider_cls = provider_class(entry["type"], name)
    try:
        return provider_cls.from_settings(name, entry)
    except ValueError as exc:  # bad option in the JSON entry
        raise ImproperlyConfigured(str(exc)) from exc


def get_provider(name: str | None = None) -> Provider:
    """Return the named provider, or the default one from ai_providers.json."""
    name, entry = provider_entry(name)
    return build_provider(name, entry)
