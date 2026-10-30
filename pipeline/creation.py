"""Creating a task: choose who can join, snapshot the settings, lay out every item's stages."""

from collections.abc import Iterable
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from problems.models import Problem

from .eligibility import Rejection, split_eligible
from .models import ItemStage, Task, TaskItem
from .stage_plan import StageStart, initial_stages
from .stages import STAGE_ORDER, Stage, stage_order
from .task_settings import TaskSettings, TaskSettingsError, settings_with_overrides


class NothingToDo(Exception):
    """None of the chosen problems can go into a task. str(exc) lists the reasons."""


@dataclass(frozen=True)
class ProblemPlan:
    problem: Problem
    stages: dict[Stage, StageStart]


@dataclass(frozen=True)
class TaskCreation:
    task: Task
    rejected: list[Rejection]


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


def default_task_name(count: int) -> str:
    when = timezone.localtime().strftime("%b %d, %H:%M")
    noun = "problem" if count == 1 else "problems"
    return f"{count} {noun} · {when}"


def first_open_stage(plan: ProblemPlan) -> Stage:
    """The first stage that still has work to do: where the item starts."""
    for stage in STAGE_ORDER:
        if not plan.stages[stage].skipped:
            return stage
    return STAGE_ORDER[
        -1
    ]  # saving notes is never pre-skipped, so this is just a safe fallback


def make_stage_rows(item: TaskItem, plan: ProblemPlan) -> list[ItemStage]:
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
def create_task(
    problems: Iterable[Problem],
    overrides: dict | None = None,
    *,
    name: str = "",
) -> TaskCreation:
    """Create a task from these problems and this run's settings (on top of your defaults).

    Problems that can't join are returned in `rejected` with their reasons. Raises
    NothingToDo if none can, and TaskSettingsError for a bad setting or no output folder.
    """
    settings = settings_with_overrides(overrides)
    check_output_folders(settings)
    eligible, rejected = split_eligible(unique_problems(problems))
    if not eligible:
        raise NothingToDo(describe_rejections(rejected) or "No problems were chosen.")

    plans = plan_problems(eligible, settings)
    task = Task.objects.create(
        name=name or default_task_name(len(plans)),
        settings=settings.model_dump(),
    )
    for position, plan in enumerate(plans):
        item = TaskItem.objects.create(
            task=task,
            problem=plan.problem,
            position=position,
            stage=first_open_stage(plan),
        )
        ItemStage.objects.bulk_create(make_stage_rows(item, plan))
    return TaskCreation(task, rejected)
