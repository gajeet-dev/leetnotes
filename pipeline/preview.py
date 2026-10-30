"""A preview of what a task would do, shown before it is created."""

from collections.abc import Iterable
from dataclasses import dataclass, field

from notes.models import NoteRecord
from notes.state import NoteState, inspect_note
from problems.models import Problem

from .creation import ProblemPlan, plan_problems, unique_problems
from .eligibility import Rejection, split_eligible
from .stages import STAGE_ORDER, Stage
from .task_settings import TaskSettings, settings_with_overrides

EDITED_STATES = {NoteState.MODIFIED, NoteState.UNTRACKED_FILE}
FETCH_STAGES = (Stage.FETCH_PROBLEM, Stage.FETCH_IMAGES, Stage.FETCH_SUBMISSION)


@dataclass(frozen=True)
class PlanPreview:
    eligible: int
    rejected: list[Rejection]
    stage_runs: dict[Stage, int]  # how many problems will actually run each stage
    needs_fetching: int  # problems with at least one fetch stage to run
    edited_notes: int  # existing notes you edited, which will be kept
    warnings: list[str] = field(default_factory=list)


def count_stage_runs(plans: list[ProblemPlan]) -> dict[Stage, int]:
    return {
        stage: sum(not plan.stages[stage].skipped for plan in plans)
        for stage in STAGE_ORDER
    }


def count_needing_fetch(plans: list[ProblemPlan]) -> int:
    return sum(
        any(not plan.stages[stage].skipped for stage in FETCH_STAGES) for plan in plans
    )


def count_edited_notes(problems: Iterable[Problem], output_dirs: list[str]) -> int:
    """Notes already in the output folders that you edited (or that predate tracking)."""
    return sum(
        inspect_note(problem, kind, folder).state in EDITED_STATES
        for problem in problems
        for kind in NoteRecord.Kind.values
        for folder in output_dirs
    )


def preview_warnings(settings: TaskSettings) -> list[str]:
    if not settings.output_dirs:
        return ["No output folder is set. Choose one in Settings first."]
    return []


def preview_plan(
    problems: Iterable[Problem], overrides: dict | None = None
) -> PlanPreview:
    settings = settings_with_overrides(overrides)
    eligible, rejected = split_eligible(unique_problems(problems))
    plans = plan_problems(eligible, settings)
    kept = (
        0
        if settings.replace_edited
        else count_edited_notes(eligible, settings.output_dirs)
    )
    return PlanPreview(
        eligible=len(plans),
        rejected=rejected,
        stage_runs=count_stage_runs(plans),
        needs_fetching=count_needing_fetch(plans),
        edited_notes=kept,
        warnings=preview_warnings(settings),
    )


def plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def describe_preview(preview: PlanPreview) -> str:
    """One line, like '2 problems: 1 to fetch, 2 AI notes to generate, 1 edited note will be kept.'"""
    if not preview.eligible:
        return "Nothing to do."
    details = []
    if preview.needs_fetching:
        details.append(f"{preview.needs_fetching} to fetch")
    if preview.stage_runs[Stage.GENERATE_PREFILL]:
        details.append(
            f"{plural(preview.stage_runs[Stage.GENERATE_PREFILL], 'AI note')} to generate"
        )
    if preview.edited_notes:
        details.append(f"{plural(preview.edited_notes, 'edited note')} will be kept")
    summary = plural(preview.eligible, "problem")
    return f"{summary}: {', '.join(details) if details else 'ready to save notes'}."
