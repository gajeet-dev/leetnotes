"""Provider settings: the built-in definitions with the user's changes on top."""

from django.core.exceptions import ImproperlyConfigured

from userconfig import AppConfig, ConfigError, load_config

from .defaults import BUILTIN_PROVIDERS, DEFAULT_PROVIDER_NAME


def saved_config() -> AppConfig:
    try:
        return load_config()
    except ConfigError as exc:
        raise ImproperlyConfigured(str(exc)) from exc


def merge_providers(saved: dict[str, dict]) -> dict[str, dict]:
    """Every provider by name: its built-in definition with the saved options on top."""
    custom = [name for name in saved if name not in BUILTIN_PROVIDERS]
    return {
        name: {**BUILTIN_PROVIDERS.get(name, {}), **saved.get(name, {})}
        for name in [*BUILTIN_PROVIDERS, *custom]
    }


def check_types(providers: dict[str, dict]) -> None:
    for name, entry in providers.items():
        if "type" not in entry:
            raise ImproperlyConfigured(f'provider "{name}" needs a "type"')


def all_providers() -> dict[str, dict]:
    providers = merge_providers(saved_config().providers)
    check_types(providers)
    return providers


def provider_names() -> list[str]:
    return list(all_providers())


def default_provider_name() -> str:
    name = saved_config().default_provider or DEFAULT_PROVIDER_NAME
    providers = all_providers()
    if name not in providers:
        known = ", ".join(providers)
        raise ImproperlyConfigured(
            f"Default AI provider {name!r} doesn't exist. Known: {known}"
        )
    return name


def provider_entry(name: str | None = None) -> tuple[str, dict]:
    """Return (name, settings) for the named provider, or for the default one."""
    providers = all_providers()
    name = name or default_provider_name()
    if name not in providers:
        known = ", ".join(providers)
        raise ImproperlyConfigured(f"Unknown AI provider {name!r}. Known: {known}")
    return name, providers[name]
