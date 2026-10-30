"""The stages that produce and save the notes: prefill, review, save notes."""

from django.core.exceptions import ImproperlyConfigured

from notes.generation import (
    ProblemNotesResult,
    describe_problem_note,
    describe_solution_note,
    generate_notes,
)
from prefill.models import AIResponse
from prefill.prompt_builder import PromptInputError
from prefill.providers.base import Provider, ProviderError, ProviderUnavailable
from prefill.services import generate_for_problem

from .handlers import StageDeps, current_problem
from .models import TaskItem
from .outcomes import PauseTask, StageError, StageOutcome, done, waiting
from .task_settings import MANUAL_PROVIDER, TaskSettings

# --- generate prefill ---


def ask_for_manual_reply(reason: str) -> StageOutcome:
    return waiting(TaskItem.InputKind.MANUAL_PREFILL, reason)


def load_provider(settings: TaskSettings, deps: StageDeps) -> Provider:
    try:
        return deps.get_provider(settings.provider)
    except ImproperlyConfigured as exc:
        # A settings problem affects every problem in the task, not just this one.
        raise PauseTask(f"AI provider problem: {exc}") from exc


def provider_failed(
    settings: TaskSettings,
    provider: Provider,
    reason: str,
    *,
    unavailable: bool = False,
) -> StageOutcome:
    """The tool couldn't give a usable note: ask for a manual reply, or fail, or pause."""
    message = f"{provider.name}: {reason}"
    if settings.manual_fallback:
        return ask_for_manual_reply(f"{message}. Paste a reply instead.")
    if unavailable:
        raise PauseTask(message)  # every later problem would fail the same way
    raise StageError(message)


def generate_with_provider(
    item: TaskItem, settings: TaskSettings, deps: StageDeps
) -> StageOutcome:
    provider = load_provider(settings, deps)
    try:
        response = generate_for_problem(current_problem(item), provider)
    except PromptInputError as exc:
        raise StageError(f"can't build the prompt: {exc}") from exc
    except ProviderError as exc:
        return provider_failed(
            settings,
            provider,
            str(exc),
            unavailable=isinstance(exc, ProviderUnavailable),
        )
    if response.status != AIResponse.Status.VALID:
        return provider_failed(
            settings, provider, f"the reply wasn't valid: {response.error}"
        )
    return done(f"{provider.name} ({response.model_name or provider.model})")


def generate_prefill(
    item: TaskItem, settings: TaskSettings, deps: StageDeps
) -> StageOutcome:
    if settings.provider == MANUAL_PROVIDER:
        return ask_for_manual_reply("Paste a reply from your AI chat.")
    return generate_with_provider(item, settings, deps)


# --- review ---


def review(item: TaskItem, settings: TaskSettings, deps: StageDeps) -> StageOutcome:
    """Wait for your approval. Approving is recorded by the action that marks this stage done."""
    if not AIResponse.objects.filter(
        problem_id=item.problem_id, is_active=True
    ).exists():
        raise StageError("there is no AI note to review")
    return waiting(TaskItem.InputKind.REVIEW, "Approve the AI note before it is saved.")


# --- save notes ---


def note_problems(result: ProblemNotesResult) -> list[str]:
    """Everything that stopped a note from being saved, one line each."""
    lines = []
    for label, note in (
        ("problem note", result.problem_note),
        ("solution note", result.solution_note),
    ):
        if note.skipped_reason:
            lines.append(f"{label} skipped ({note.skipped_reason})")
        lines += [
            f"{label}: {folder}: {error}" for folder, error in note.errors.items()
        ]
    return lines


def describe_saved(result: ProblemNotesResult) -> str:
    return f"{describe_problem_note(result.problem_note)}; {describe_solution_note(result.solution_note)}"


def save_notes(item: TaskItem, settings: TaskSettings, deps: StageDeps) -> StageOutcome:
    if not settings.output_dirs:
        raise PauseTask("No output folder is set. Choose one in Settings.")
    result = generate_notes(
        current_problem(item),
        settings.output_dirs,
        replace_edited=settings.replace_edited,
    )
    if problems := note_problems(result):
        raise StageError("; ".join(problems))
    return done(describe_saved(result))
