"""Getting back to a sane state after the app stopped in the middle of work."""

from .engine import next_pending_stage
from .models import ItemStage, TaskItem
from .transitions import complete_item, put_back, refresh_task_status, requeue_item


def put_back_interrupted_stages() -> int:
    """A stage that was running when the app stopped never finished: it will run again."""
    rows = list(
        ItemStage.objects.filter(status=ItemStage.Status.RUNNING).select_related(
            "item", "item__task"
        )
    )
    for row in rows:
        put_back(row.item, row)
    return len(rows)


def settle_item(item: TaskItem) -> None:
    """Continue an item from where its saved stages say it is."""
    following = next_pending_stage(item)
    if following is None:
        complete_item(item)
        refresh_task_status(item.task)
    else:
        requeue_item(item, following.stage)


def settle_stray_items() -> int:
    """An item marked running with no running stage was interrupted between two saves."""
    stray = list(
        TaskItem.objects.filter(status=TaskItem.Status.RUNNING).select_related("task")
    )
    for item in stray:
        settle_item(item)
    return len(stray)


def recover_interrupted() -> int:
    """Fix everything the last run left half done. Returns how many stages and items were fixed.

    Call this only when no worker is running (the worker does, once it holds its lock):
    then nothing can really be running, so anything marked running is left over.
    """
    return put_back_interrupted_stages() + settle_stray_items()
