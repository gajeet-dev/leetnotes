"""The settings a pipeline item runs with: a snapshot taken when it is created."""

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from prefill.providers.config import default_provider_name, provider_names
from userconfig import effective, load_config
from userconfig.store import describe_validation_errors

MANUAL_PROVIDER = "manual"  # no tool: you paste a reply from a web chat


class TaskSettingsError(ValueError):
    """A setting is unknown or invalid. str(exc) says which."""


class TaskSettings(BaseModel):
    model_config = ConfigDict(
        extra="ignore"
    )  # snapshots saved by an older version still load

    provider: str = MANUAL_PROVIDER  # a provider name, or "manual"
    model: str = ""  # optional model override (e.g. claude-3-5-sonnet, gemini-1.5-flash)
    review: bool = True  # wait for your approval before saving notes
    output_dirs: list[str] = Field(default_factory=list)
    replace_edited: bool = False  # replace notes you edited (backed up first)
    refetch: bool = False  # fetch data again even if the problem already has it
    refetch_problem: bool = False
    refetch_images: bool = False
    refetch_submission: bool = False
    regenerate_prefill: bool = (
        False  # ask the AI again even if there is a current AI note
    )
    use_existing_prefill: bool = (
        True  # use existing AI note if available
    )
    manual_fallback: bool = (
        True  # if the AI tool fails, ask you to paste a reply instead
    )


def default_task_settings() -> TaskSettings:
    """What a new item starts from: your saved defaults."""
    cfg = load_config()
    default_prov = default_provider_name()
    default_mod = ""
    if default_prov == "agy":
        default_mod = cfg.agy.default_model
    elif default_prov in cfg.providers:
        default_mod = cfg.providers[default_prov].default_model

    return TaskSettings(
        provider=default_prov,
        model=default_mod,
        review=cfg.review_before_writing,
        output_dirs=[str(folder) for folder in effective.output_dirs()],
    )


def check_override_names(overrides: dict) -> None:
    unknown = sorted(set(overrides) - set(TaskSettings.model_fields))
    if unknown:
        raise TaskSettingsError(f"Unknown pipeline setting(s): {', '.join(unknown)}")


def check_provider(name: str) -> None:
    if name == MANUAL_PROVIDER:
        return
    known = provider_names()
    if name not in known:
        raise TaskSettingsError(
            f"Unknown AI provider {name!r}. Choose {MANUAL_PROVIDER} or one of: {', '.join(known)}"
        )


def settings_with_overrides(overrides: dict | None = None) -> TaskSettings:
    """Your defaults with this item's changes on top, validated."""
    overrides = overrides or {}
    check_override_names(overrides)
    merged = {**default_task_settings().model_dump(), **overrides}
    try:
        settings = TaskSettings.model_validate(merged)
    except ValidationError as exc:
        raise TaskSettingsError(describe_validation_errors(exc)) from exc
    check_provider(settings.provider)
    return settings


def task_settings(item_or_dict) -> TaskSettings:
    """The snapshot saved on an item (or settings dict)."""
    if hasattr(item_or_dict, "settings"):
        raw = item_or_dict.settings
    elif isinstance(item_or_dict, dict):
        raw = item_or_dict
    else:
        raw = {}
    return TaskSettings.model_validate(raw or default_task_settings().model_dump())


item_settings = task_settings
