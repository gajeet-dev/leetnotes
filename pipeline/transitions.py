"""How the statuses of a task, an item and its stages change. Nothing in here does any work."""

from django.utils import timezone

from .models import ItemStage, Task, TaskItem
from .outcomes import StageOutcome

FINISHED_ITEM_STATUSES = (TaskItem.Status.COMPLETED, TaskItem.Status.FAILED)


def save_fields(obj, **values) -> None:
    """Set these fields on a model instance and save only them."""
    for name, value in values.items():
        setattr(obj, name, value)
    fields = list(values)
    if hasattr(obj, "updated_at"):
        fields.append("updated_at")  # auto_now only updates when it is listed
    obj.save(update_fields=fields)


# --- a stage starts, ends, waits or fails ---


def begin_stage(item: TaskItem, row: ItemStage) -> None:
    """The stage starts running, and so does the item (and the task, the first time)."""
    now = timezone.now()
    save_fields(
        row, status=ItemStage.Status.RUNNING, started_at=now, finished_at=None, error=""
    )
    save_fields(
        item,
        status=TaskItem.Status.RUNNING,
        stage=row.stage,
        input_kind="",
        error="",
        started_at=item.started_at or now,
    )
    if item.task.started_at is None:
        save_fields(item.task, started_at=now)


def complete_stage(row: ItemStage, outcome: StageOutcome) -> None:
    """The stage is over: done, or skipped because it wasn't needed."""
    save_fields(
        row, status=outcome.status, detail=outcome.detail, finished_at=timezone.now()
    )


def park_item(item: TaskItem, row: ItemStage, outcome: StageOutcome) -> None:
    """The stage needs you. The item waits, and the engine moves on to other items."""
    save_fields(
        row, status=ItemStage.Status.WAITING, detail=outcome.detail, finished_at=None
    )
    save_fields(item, status=TaskItem.Status.NEEDS_INPUT, input_kind=outcome.input_kind)


def apply_outcome(item: TaskItem, row: ItemStage, outcome: StageOutcome) -> None:
    if outcome.status == ItemStage.Status.WAITING:
        park_item(item, row, outcome)
    else:
        complete_stage(row, outcome)


def fail_stage(item: TaskItem, row: ItemStage, message: str) -> None:
    """The stage failed for this problem, so the item fails with it. Other items carry on."""
    now = timezone.now()
    save_fields(row, status=ItemStage.Status.FAILED, error=message, finished_at=now)
    save_fields(item, status=TaskItem.Status.FAILED, error=message, finished_at=now)


def put_back(item: TaskItem, row: ItemStage) -> None:
    """Undo a stage that couldn't really run, so it is tried again later."""
    save_fields(row, status=ItemStage.Status.PENDING, started_at=None, finished_at=None)
    save_fields(item, status=TaskItem.Status.QUEUED)


def complete_item(item: TaskItem) -> None:
    """Every stage is done or skipped."""
    save_fields(
        item,
        status=TaskItem.Status.COMPLETED,
        input_kind="",
        finished_at=timezone.now(),
    )


# --- the task as a whole ---


def pause_for_error(item: TaskItem, row: ItemStage, reason: str) -> None:
    """Something affecting every problem is wrong, so the whole task pauses.

    The stage that hit it goes back to pending: it didn't really run, and it
    will be tried again when you resume.
    """
    put_back(item, row)
    pause_task(
        Task.objects.get(pk=item.task_id), reason
    )  # does nothing if it was cancelled meanwhile


def pause_task(task: Task, reason: str = "Paused by you") -> None:
    if task.status == Task.Status.ACTIVE:
        save_fields(task, status=Task.Status.PAUSED, pause_reason=reason)


def resume_task(task: Task) -> None:
    if task.status == Task.Status.PAUSED:
        save_fields(task, status=Task.Status.ACTIVE, pause_reason="")


def cancel_task(task: Task) -> None:
    """Stop the task for good. Finished and already cancelled tasks are left as they are."""
    if task.status in (Task.Status.ACTIVE, Task.Status.PAUSED):
        save_fields(task, status=Task.Status.CANCELLED, finished_at=timezone.now())


def task_is_done(task: Task) -> bool:
    """True when no item is still queued, running or waiting for you."""
    return not task.items.exclude(status__in=FINISHED_ITEM_STATUSES).exists()


def refresh_task_status(task: Task) -> None:
    """Finish the task once all its items are completed or failed."""
    if task.status == Task.Status.ACTIVE and task_is_done(task):
        save_fields(task, status=Task.Status.FINISHED, finished_at=timezone.now())


def requeue_item(item: TaskItem, next_stage: str) -> None:
    """A stage finished and more remain: the item goes back in the queue, ready for the next stage."""
    save_fields(item, status=TaskItem.Status.QUEUED, stage=next_stage)
