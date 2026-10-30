"""For one problem, which stages still have to run, and which can start out skipped."""

from dataclasses import dataclass
from enum import StrEnum

from prefill.models import AIResponse
from prefill.prompt_builder import PromptInputError, build_prompt
from problems.models import Problem, SyncTask

from .models import ItemStage
from .stages import Stage
from .task_settings import TaskSettings

ALREADY_FETCHED = "already fetched"
PINNED_KEPT = "pinned solution kept"
AI_NOTE_EXISTS = "AI note already exists"
REVIEW_OFF = "review is off"
EXISTING_AI_NOTE = "existing AI note"
SOLUTION_CHANGED = "the solution changed since the AI note was made"


@dataclass(frozen=True)
class StageStart:
    """How a stage begins: waiting to run, or already skipped with a reason."""

    status: str
    detail: str = ""

    @property
    def skipped(self) -> bool:
        return self.status == ItemStage.Status.SKIPPED


PENDING = StageStart(ItemStage.Status.PENDING)


def skip(detail: str) -> StageStart:
    return StageStart(ItemStage.Status.SKIPPED, detail)


class AiNoteState(StrEnum):
    NONE = "none"  # no active AI note
    FRESH = "fresh"  # there is one, and it matches the current problem and code
    STALE = "stale"  # there is one, but the code or statement changed since


# --- what is stored ---


def fetch_statuses(problem: Problem) -> dict[str, str]:
    """The sync status of each part of the problem, by part name."""
    return dict(problem.sync_tasks.values_list("part", "status"))


def is_fetched(statuses: dict[str, str], part: str) -> bool:
    return statuses.get(part) == SyncTask.Status.DONE


def has_pinned_submission(problem: Problem) -> bool:
    return problem.submissions.filter(pinned=True).exists()


def ai_note_state(problem: Problem) -> AiNoteState:
    response = AIResponse.objects.filter(problem=problem, is_active=True).first()
    if response is None:
        return AiNoteState.NONE
    try:
        current_hash = build_prompt(problem).input_hash
    except PromptInputError:
        return AiNoteState.STALE  # can't compare: the data isn't there
    return (
        AiNoteState.FRESH if response.input_hash == current_hash else AiNoteState.STALE
    )


# --- one rule per stage ---


def plan_fetch_problem(statuses: dict[str, str], refetch: bool) -> StageStart:
    if is_fetched(statuses, SyncTask.Part.CONTENT) and not refetch:
        return skip(ALREADY_FETCHED)
    return PENDING


def plan_fetch_images(
    statuses: dict[str, str], fetch_problem: StageStart
) -> StageStart:
    """Images are redone whenever the problem's content is, since new HTML invalidates them."""
    if is_fetched(statuses, SyncTask.Part.IMAGES) and fetch_problem.skipped:
        return skip(ALREADY_FETCHED)
    return PENDING


def plan_fetch_submission(
    problem: Problem, statuses: dict[str, str], refetch: bool
) -> StageStart:
    if refetch and has_pinned_submission(problem):
        return skip(PINNED_KEPT)
    if is_fetched(statuses, SyncTask.Part.SUBMISSIONS) and not refetch:
        return skip(ALREADY_FETCHED)
    return PENDING


def plan_generate_prefill(
    problem: Problem, regenerate: bool, data_unchanged: bool
) -> StageStart:
    """Skipped only when nothing is changing and the current AI note still fits the code."""
    if regenerate or not data_unchanged:
        return PENDING
    state = ai_note_state(problem)
    if state is AiNoteState.FRESH:
        return skip(AI_NOTE_EXISTS)
    if state is AiNoteState.STALE:
        return StageStart(ItemStage.Status.PENDING, SOLUTION_CHANGED)
    return PENDING


def plan_review(review_on: bool, prefill: StageStart) -> StageStart:
    if not review_on:
        return skip(REVIEW_OFF)
    if prefill.skipped:
        return skip(EXISTING_AI_NOTE)  # an existing note was already approved
    return PENDING


# --- all stages together ---


def initial_stages(problem: Problem, settings: TaskSettings) -> dict[Stage, StageStart]:
    """How each stage of the pipeline begins for this problem, in pipeline order."""
    statuses = fetch_statuses(problem)
    fetch_problem = plan_fetch_problem(statuses, settings.refetch)
    fetch_images = plan_fetch_images(statuses, fetch_problem)
    fetch_submission = plan_fetch_submission(problem, statuses, settings.refetch)
    data_unchanged = fetch_problem.skipped and fetch_submission.skipped
    prefill = plan_generate_prefill(
        problem, settings.regenerate_prefill, data_unchanged
    )
    review = plan_review(settings.review, prefill)
    return {
        Stage.FETCH_PROBLEM: fetch_problem,
        Stage.FETCH_IMAGES: fetch_images,
        Stage.FETCH_SUBMISSION: fetch_submission,
        Stage.GENERATE_PREFILL: prefill,
        Stage.REVIEW: review,
        Stage.SAVE_NOTES: PENDING,  # the writer decides per file whether anything needs doing
    }
