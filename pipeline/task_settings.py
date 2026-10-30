"""The settings a task runs with: a snapshot taken when the task is created."""

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from prefill.providers.config import default_provider_name, provider_names
from userconfig import effective, load_config
from userconfig.store import describe_validation_errors

from .models import Task

MANUAL_PROVIDER = "manual"  # no tool: you paste a reply from a web chat


class TaskSettingsError(ValueError):
    """A task setting is unknown or invalid. str(exc) says which."""


class TaskSettings(BaseModel):
    model_config = ConfigDict(
        extra="ignore"
    )  # snapshots saved by an older version still load

    provider: str = MANUAL_PROVIDER  # a provider name, or "manual"
    review: bool = True  # wait for your approval before saving notes
    output_dirs: list[str] = Field(default_factory=list)
    replace_edited: bool = False  # replace notes you edited (backed up first)
    refetch: bool = False  # fetch data again even if the problem already has it
    regenerate_prefill: bool = (
        False  # ask the AI again even if there is a current AI note
    )
    manual_fallback: bool = (
        True  # if the AI tool fails, ask you to paste a reply instead
    )


def default_task_settings() -> TaskSettings:
    """What a new task starts from: your saved defaults."""
    return TaskSettings(
        provider=default_provider_name(),
        review=load_config().review_before_writing,
        output_dirs=[str(folder) for folder in effective.output_dirs()],
    )


def check_override_names(overrides: dict) -> None:
    unknown = sorted(set(overrides) - set(TaskSettings.model_fields))
    if unknown:
        raise TaskSettingsError(f"Unknown task setting(s): {', '.join(unknown)}")


def check_provider(name: str) -> None:
    if name == MANUAL_PROVIDER:
        return
    known = provider_names()
    if name not in known:
        raise TaskSettingsError(
            f"Unknown AI provider {name!r}. Choose {MANUAL_PROVIDER} or one of: {', '.join(known)}"
        )


def settings_with_overrides(overrides: dict | None = None) -> TaskSettings:
    """Your defaults with this task's changes on top, validated."""
    overrides = overrides or {}
    check_override_names(overrides)
    merged = {**default_task_settings().model_dump(), **overrides}
    try:
        settings = TaskSettings.model_validate(merged)
    except ValidationError as exc:
        raise TaskSettingsError(describe_validation_errors(exc)) from exc
    check_provider(settings.provider)
    return settings


def task_settings(task: Task) -> TaskSettings:
    """The snapshot saved on a task."""
    return TaskSettings.model_validate(task.settings)
