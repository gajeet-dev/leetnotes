"""Changing provider settings from the UI: validated first, then saved to config.json."""

from django.core.exceptions import ImproperlyConfigured

from userconfig import ConfigError, update_config

from .config import all_providers, saved_config
from .defaults import BUILTIN_PROVIDERS
from .registry import build_provider


def save_changes(changes: dict) -> None:
    try:
        update_config(changes)
    except ConfigError as exc:
        raise ImproperlyConfigured(str(exc)) from exc


def change_provider(name: str, changes: dict) -> dict:
    """Change some options of a provider, or define a new one. Returns the resulting definition."""
    user = dict(saved_config().providers)
    user_entry = {**user.get(name, {}), **changes}
    definition = {**BUILTIN_PROVIDERS.get(name, {}), **user_entry}
    if "type" not in definition:
        raise ImproperlyConfigured(f'provider "{name}" needs a "type"')
    build_provider(
        name, definition
    )  # raises for a bad option, before anything is saved
    save_changes({"providers": {**user, name: user_entry}})
    return definition


def reset_provider(name: str) -> None:
    """Drop your changes to a built-in provider, or delete a custom one."""
    config = saved_config()
    user = {key: value for key, value in config.providers.items() if key != name}
    changes: dict = {"providers": user}
    if config.default_provider == name and name not in BUILTIN_PROVIDERS:
        changes["default_provider"] = (
            None  # the deleted provider can't stay the default
        )
    save_changes(changes)


def set_default_provider(name: str | None) -> None:
    """Choose the provider used when none is named. None goes back to the built-in default."""
    providers = all_providers()
    if name is not None and name not in providers:
        raise ImproperlyConfigured(
            f"Unknown AI provider {name!r}. Known: {', '.join(providers)}"
        )
    save_changes({"default_provider": name})
