"""Starting pipeline items for problems: snapshot the settings, lay out every item's stages."""

from collections.abc import Iterable
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from pipeline.models import ItemStage, PipelineItem
from pipeline.runtime.worker import ensure_worker_running
from pipeline.stages.definitions import STAGE_ORDER, Stage, stage_order
from pipeline.stages.plan import StageStart, initial_stages
from pipeline.tasks.settings import (
    TaskSettings,
    TaskSettingsError,
    settings_with_overrides,
)
from problems.models import Problem

from .eligibility import Rejection, split_eligible


class NothingToDo(Exception):
    """None of the chosen problems can go into the pipeline. str(exc) lists the reasons."""


@dataclass(frozen=True)
class ProblemPlan:
    problem: Problem
    stages: dict[Stage, StageStart]


@dataclass(frozen=True)
class PipelineStart:
    items: list[PipelineItem]
    rejected: list[Rejection]


# Backward-compatible alias
TaskCreation = PipelineStart


def unique_problems(problems: Iterable[Problem]) -> list[Problem]:
    """Each problem once, in the order given."""
    return list({problem.pk: problem for problem in problems}.values())


def check_output_folders(settings: TaskSettings) -> None:
    if not settings.output_dirs:
        raise TaskSettingsError(
            "No output folder is set. Choose one in Settings first."
        )


def plan_problems(
    problems: Iterable[Problem], settings: TaskSettings
) -> list[ProblemPlan]:
    return [
        ProblemPlan(problem, initial_stages(problem, settings)) for problem in problems
    ]


def describe_rejections(rejected: list[Rejection]) -> str:
    return "; ".join(f"{item.problem.slug}: {item.reason}" for item in rejected)


def first_open_stage(plan: ProblemPlan) -> Stage:
    """The first stage that still has work to do: where the item starts."""
    for stage in STAGE_ORDER:
        if not plan.stages[stage].skipped:
            return stage
    return STAGE_ORDER[-1]


def make_stage_rows(item: PipelineItem, plan: ProblemPlan) -> list[ItemStage]:
    return [
        ItemStage(
            item=item,
            stage=stage,
            order=stage_order(stage),
            status=plan.stages[stage].status,
            detail=plan.stages[stage].detail,
        )
        for stage in STAGE_ORDER
    ]


@transaction.atomic
def start_pipeline_items(
    problems: Iterable[Problem],
    overrides: dict | None = None,
    wake=ensure_worker_running,
) -> PipelineStart:
    """Start pipeline runs for these problems with snapshot settings.

    Problems that can't run are returned in `rejected` with their reasons.
    Raises NothingToDo if none can, and TaskSettingsError for a bad setting.
    """
    settings = settings_with_overrides(overrides)
    check_output_folders(settings)
    unique = unique_problems(problems)
    eligible, rejected = split_eligible(unique)
    if not eligible:
        raise NothingToDo(describe_rejections(rejected) or "No problems were chosen.")

    plans = plan_problems(eligible, settings)
    created_items: list[PipelineItem] = []

    for position, plan in enumerate(plans):
        # Cancel any previous active or paused runs for this problem
        PipelineItem.objects.filter(
            problem=plan.problem,
            status__in=[
                PipelineItem.Status.QUEUED,
                PipelineItem.Status.RUNNING,
                PipelineItem.Status.NEEDS_INPUT,
                PipelineItem.Status.PAUSED,
            ],
        ).update(
            status=PipelineItem.Status.CANCELLED,
            finished_at=timezone.now(),
        )

        item = PipelineItem.objects.create(
            problem=plan.problem,
            position=position,
            status=PipelineItem.Status.QUEUED,
            stage=first_open_stage(plan),
            settings=settings.model_dump(),
        )
        ItemStage.objects.bulk_create(make_stage_rows(item, plan))
        created_items.append(item)

    if wake:
        wake()

    return PipelineStart(created_items, rejected)


def create_task(
    problems: Iterable[Problem],
    overrides: dict | None = None,
    *,
    name: str = "",
) -> PipelineStart:
    """Backward compatibility alias for start_pipeline_items."""
    return start_pipeline_items(problems, overrides=overrides, wake=None)
