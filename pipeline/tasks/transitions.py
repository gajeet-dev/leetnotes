"""How the statuses of a pipeline item and its stages change. Nothing in here does any work."""

from django.utils import timezone

from pipeline.models import ItemStage, PipelineItem
from pipeline.stages.outcomes import StageOutcome

FINISHED_ITEM_STATUSES = (
    PipelineItem.Status.COMPLETED,
    PipelineItem.Status.FAILED,
    PipelineItem.Status.CANCELLED,
)


def save_fields(obj, **values) -> None:
    """Set these fields on a model instance and save only them."""
    for name, value in values.items():
        setattr(obj, name, value)
    fields = list(values)
    if hasattr(obj, "updated_at"):
        fields.append("updated_at")  # auto_now only updates when it is listed
    obj.save(update_fields=fields)


# --- a stage starts, ends, waits or fails ---


def begin_stage(item: PipelineItem, row: ItemStage) -> None:
    """The stage starts running, and so does the item."""
    now = timezone.now()
    save_fields(
        row, status=ItemStage.Status.RUNNING, started_at=now, finished_at=None, error=""
    )
    save_fields(
        item,
        status=PipelineItem.Status.RUNNING,
        stage=row.stage,
        input_kind="",
        error="",
        started_at=item.started_at or now,
    )


def complete_stage(row: ItemStage, outcome: StageOutcome) -> None:
    """The stage is over: done, or skipped because it wasn't needed."""
    save_fields(
        row, status=outcome.status, detail=outcome.detail, finished_at=timezone.now()
    )


def park_item(item: PipelineItem, row: ItemStage, outcome: StageOutcome) -> None:
    """The stage needs you. The item waits, and the engine moves on to other items."""
    save_fields(
        row, status=ItemStage.Status.WAITING, detail=outcome.detail, finished_at=None
    )
    save_fields(
        item, status=PipelineItem.Status.NEEDS_INPUT, input_kind=outcome.input_kind
    )


def apply_outcome(item: PipelineItem, row: ItemStage, outcome: StageOutcome) -> None:
    if outcome.status == ItemStage.Status.WAITING:
        park_item(item, row, outcome)
    else:
        complete_stage(row, outcome)


def fail_stage(item: PipelineItem, row: ItemStage, message: str) -> None:
    """The stage failed for this problem, so the item fails with it. Other items carry on."""
    now = timezone.now()
    save_fields(row, status=ItemStage.Status.FAILED, error=message, finished_at=now)
    save_fields(item, status=PipelineItem.Status.FAILED, error=message, finished_at=now)


def put_back(item: PipelineItem, row: ItemStage) -> None:
    """Undo a stage that couldn't really run, so it is tried again later."""
    save_fields(row, status=ItemStage.Status.PENDING, started_at=None, finished_at=None)
    if item.status != PipelineItem.Status.CANCELLED:
        save_fields(item, status=PipelineItem.Status.QUEUED)


def complete_item(item: PipelineItem) -> None:
    """Every stage is done or skipped."""
    save_fields(
        item,
        status=PipelineItem.Status.COMPLETED,
        input_kind="",
        finished_at=timezone.now(),
    )


def requeue_item(item: PipelineItem, next_stage: str) -> None:
    """A stage finished and more remain: the item goes back in the queue, ready for the next stage."""
    save_fields(item, status=PipelineItem.Status.QUEUED, stage=next_stage)


# --- Item pausing, resuming and cancelling ---


def pause_item(item: PipelineItem, reason: str = "Paused by you") -> None:
    if item.status in (
        PipelineItem.Status.QUEUED,
        PipelineItem.Status.RUNNING,
        PipelineItem.Status.NEEDS_INPUT,
    ):
        save_fields(item, status=PipelineItem.Status.PAUSED, error=reason)


def resume_item(item: PipelineItem) -> None:
    if item.status == PipelineItem.Status.PAUSED:
        save_fields(item, status=PipelineItem.Status.QUEUED, error="")


def cancel_item(item: PipelineItem) -> None:
    """Stop this item. Finished and already cancelled items are left as they are."""
    if item.status in (
        PipelineItem.Status.QUEUED,
        PipelineItem.Status.RUNNING,
        PipelineItem.Status.NEEDS_INPUT,
        PipelineItem.Status.PAUSED,
    ):
        save_fields(
            item, status=PipelineItem.Status.CANCELLED, finished_at=timezone.now()
        )


def pause_for_error(item: PipelineItem, row: ItemStage, reason: str) -> None:
    """An account-level error occurred (e.g. AuthError or RateLimit).

    The stage that hit it goes back to pending so it can be retried when resumed.
    """
    put_back(item, row)
    pause_item(item, reason)


# Legacy no-op helper for backwards compatibility
def refresh_task_status(task=None) -> None:
    pass
