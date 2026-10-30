"""The engine: runs one stage of one item at a time and records what happened."""

from dataclasses import dataclass
from collections.abc import Callable
import structlog
from structlog.contextvars import bound_contextvars

from leetcode.connection import result_for_error
from leetcode.exceptions import AuthError, LeetCodeError, NotConfigured, RateLimitError

from .handlers import StageDeps
from .models import ItemStage, Task, TaskItem
from .outcomes import PauseTask, StageError, StageOutcome
from .stage_registry import handler_for
from .task_settings import task_settings
from .transitions import (
    apply_outcome,
    begin_stage,
    complete_item,
    fail_stage,
    pause_for_error,
    refresh_task_status,
    requeue_item,
)

logger = structlog.get_logger(__name__)

# These affect every problem, not just one, so they pause the whole task.
ACCOUNT_ERRORS = (AuthError, RateLimitError, NotConfigured)


@dataclass(frozen=True)
class StepResult:
    item: TaskItem
    stage: str | None  # the stage that ran, or None if the item had nothing left to do
    status: str  # that stage's status afterwards


# Called with the item and the stage about to run. Return False to hold back (for example,
# when the worker is shutting down). This is where pacing happens.
BeforeStage = Callable[[TaskItem, ItemStage], bool]


def still_runnable(item: TaskItem) -> bool:
    """False if the item or its task was paused, cancelled or changed while we waited."""
    item.refresh_from_db(fields=["status"])
    item.task.refresh_from_db(fields=["status"])
    return (
        item.status == TaskItem.Status.QUEUED and item.task.status == Task.Status.ACTIVE
    )


# --- choosing what to run ---


def next_queued_item(task: Task | None = None) -> TaskItem | None:
    """The next item to work on: oldest active task first, then its first queued item."""
    items = TaskItem.objects.filter(
        status=TaskItem.Status.QUEUED, task__status=Task.Status.ACTIVE
    )
    if task is not None:
        items = items.filter(task=task)
    return (
        items.select_related("task", "problem")
        .order_by("task__created_at", "task_id", "position")
        .first()
    )


def next_pending_stage(item: TaskItem) -> ItemStage | None:
    """The first stage of this item that hasn't run yet."""
    return item.stages.filter(status=ItemStage.Status.PENDING).order_by("order").first()


# --- running one stage ---


def advance_item(item: TaskItem, outcome: StageOutcome) -> None:
    """After a successful stage: finish the item, or queue its next stage. A waiting item stays put."""
    if outcome.status == ItemStage.Status.WAITING:
        return
    following = next_pending_stage(item)
    if following is None:
        complete_item(item)
    else:
        requeue_item(item, following.stage)


def run_stage(item: TaskItem, row: ItemStage, deps: StageDeps) -> None:
    """Run one stage and record the result, whatever it is."""
    begin_stage(item, row)
    try:
        outcome = handler_for(row.stage)(item, task_settings(item.task), deps)
    except PauseTask as exc:
        pause_for_error(item, row, str(exc))
    except ACCOUNT_ERRORS as exc:
        pause_for_error(item, row, result_for_error(exc).message)
    except (StageError, LeetCodeError) as exc:
        fail_stage(item, row, str(exc))
    except Exception as exc:  # a bug must fail this item, not stop the whole engine
        logger.exception("stage_crashed")
        fail_stage(item, row, f"Unexpected error: {type(exc).__name__}: {exc}")
    else:
        apply_outcome(item, row, outcome)
        advance_item(item, outcome)
    refresh_task_status(item.task)


# --- stepping ---


def step(
    deps: StageDeps | None = None,
    task: Task | None = None,
    before_stage: BeforeStage | None = None,
) -> StepResult | None:
    """Run the next stage of the next queued item.

    Returns None when there is nothing to do, or when the stage was held back: the
    hook returned False, or the item was paused or cancelled while the hook waited.
    """
    deps = deps or StageDeps()
    item = next_queued_item(task)
    if item is None:
        return None
    row = next_pending_stage(item)
    if row is None:  # nothing left to run: just finish it
        complete_item(item)
        refresh_task_status(item.task)
        return StepResult(item, None, ItemStage.Status.DONE)
    if before_stage and not (before_stage(item, row) and still_runnable(item)):
        return None
    with bound_contextvars(task=item.task_id, slug=item.problem.slug, stage=row.stage):
        run_stage(item, row, deps)
    return StepResult(item, row.stage, row.status)


def run_until_idle(
    deps: StageDeps | None = None,
    task: Task | None = None,
    max_steps: int = 10_000,
    before_stage: BeforeStage | None = None,
) -> list[StepResult]:
    """Keep stepping until nothing is left to run (or the safety limit is reached)."""
    results: list[StepResult] = []
    while (
        len(results) < max_steps
        and (result := step(deps, task, before_stage)) is not None
    ):
        results.append(result)
    return results
