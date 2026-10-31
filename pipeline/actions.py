"""What you can do about an item that is waiting for you."""

from collections.abc import Callable

from django.db import transaction

from prefill.parsing import ParseError, parse_solution
from prefill.prompt_builder import PromptInputError, build_prompt
from prefill.services import import_manual_response

from .engine import advance_item
from .models import ItemStage, Task, TaskItem
from .outcomes import done
from .stages import Stage
from .transitions import complete_stage, refresh_task_status, save_fields
from .worker import ensure_worker_running

WakeWorker = Callable[[], bool]
OPEN_TASK_STATUSES = (Task.Status.ACTIVE, Task.Status.PAUSED)


class ActionError(Exception):
    """The action doesn't apply to this item right now. str(exc) says why."""


# --- finding what is waiting ---


def get_item(item_id: int) -> TaskItem:
    return TaskItem.objects.select_related("task", "problem").get(pk=item_id)


def check_task_is_open(item: TaskItem) -> None:
    if item.task.status not in OPEN_TASK_STATUSES:
        raise ActionError(f"the task is {item.task.status}")


def check_item_waits_for(item: TaskItem, kind: str) -> None:
    if item.status != TaskItem.Status.NEEDS_INPUT or item.input_kind != kind:
        raise ActionError(
            f"{item.problem.slug} is not waiting for {kind.replace('_', ' ')}"
        )


def waiting_stage(item: TaskItem, kind: str) -> ItemStage:
    """The stage this item is parked at, after checking it really waits for `kind`."""
    check_task_is_open(item)
    check_item_waits_for(item, kind)
    return item.stages.get(stage=item.stage, status=ItemStage.Status.WAITING)


# --- moving on once you have answered ---


def finish_waiting_stage(item: TaskItem, row: ItemStage, detail: str) -> None:
    """Mark the stage done and send the item on to its next stage (or finish it)."""
    outcome = done(detail)
    complete_stage(row, outcome)
    save_fields(item, input_kind="")
    advance_item(item, outcome)
    refresh_task_status(item.task)


# --- approving reviews ---


def approve_without_waking(item: TaskItem) -> None:
    row = waiting_stage(item, TaskItem.InputKind.REVIEW)
    finish_waiting_stage(item, row, "approved")


def approve_review(item_id: int, wake: WakeWorker = ensure_worker_running) -> TaskItem:
    """Approve one AI note, so the item goes on to save its notes."""
    item = get_item(item_id)
    approve_without_waking(item)
    wake()
    return item


def items_waiting_for_review(task: Task) -> list[TaskItem]:
    return list(
        task.items.select_related("task", "problem").filter(
            status=TaskItem.Status.NEEDS_INPUT,
            input_kind=TaskItem.InputKind.REVIEW,
        )
    )


def approve_all_reviews(task_id: int, wake: WakeWorker = ensure_worker_running) -> int:
    """Approve every AI note in the task that is waiting for review. Returns how many."""
    task = Task.objects.get(pk=task_id)
    items = items_waiting_for_review(task)
    for item in items:
        approve_without_waking(item)
    if items:
        wake()
    return len(items)


# --- submitting a pasted reply ---


def check_reply_is_valid(text: str) -> None:
    try:
        parse_solution(text)
    except ParseError as exc:
        raise ActionError(f"the reply isn't valid:\n{exc}") from exc


def build_prompt_for(item: TaskItem):
    try:
        return build_prompt(item.problem)
    except PromptInputError as exc:
        raise ActionError(f"can't build the prompt: {exc}") from exc


def save_pasted_reply(item: TaskItem, row: ItemStage, text: str, label: str) -> None:
    """Store the reply as the active AI note and finish the waiting stage, together."""
    prompt = build_prompt_for(item)
    with transaction.atomic():
        import_manual_response(item.problem, text, label=label, prompt=prompt)
        finish_waiting_stage(item, row, f"manual reply ({label or 'manual'})")


def submit_manual_reply(
    item_id: int,
    text: str,
    label: str = "",
    wake: WakeWorker = ensure_worker_running,
) -> TaskItem:
    """Use a reply pasted from an AI chat as this item's AI note, then carry on."""
    item = get_item(item_id)
    row = waiting_stage(item, TaskItem.InputKind.MANUAL_PREFILL)
    check_reply_is_valid(text)
    save_pasted_reply(item, row, text, label)
    wake()
    return item


# --- putting stages back ---


def reset_stage_row(row: ItemStage) -> None:
    """Make a stage as if it had never run."""
    save_fields(
        row,
        status=ItemStage.Status.PENDING,
        detail="",
        error="",
        started_at=None,
        finished_at=None,
    )


def queue_item_at(item: TaskItem, stage: str) -> None:
    save_fields(
        item,
        status=TaskItem.Status.QUEUED,
        stage=stage,
        input_kind="",
        error="",
        finished_at=None,
    )


# --- rejecting a review ---


def reset_prefill_and_review(item: TaskItem) -> None:
    for stage in (Stage.GENERATE_PREFILL, Stage.REVIEW):
        reset_stage_row(item.stages.get(stage=stage))


def reject_review(item_id: int, wake: WakeWorker = ensure_worker_running) -> TaskItem:
    """Throw the AI note away for now: the item goes back to generating a new one."""
    item = get_item(item_id)
    waiting_stage(item, TaskItem.InputKind.REVIEW)
    reset_prefill_and_review(item)
    queue_item_at(item, Stage.GENERATE_PREFILL)
    wake()
    return item


# --- retrying a failed item ---

RETRYABLE_TASK_STATUSES = (*OPEN_TASK_STATUSES, Task.Status.FINISHED)


def check_item_can_retry(item: TaskItem) -> None:
    if item.task.status not in RETRYABLE_TASK_STATUSES:
        raise ActionError(f"the task is {item.task.status}")
    if item.status != TaskItem.Status.FAILED:
        raise ActionError(f"{item.problem.slug} has not failed")


def reopen_task_if_finished(task: Task) -> None:
    """A task that finished because this item failed has work to do again."""
    if task.status == Task.Status.FINISHED:
        save_fields(task, status=Task.Status.ACTIVE, finished_at=None)


def failed_stage(item: TaskItem) -> ItemStage:
    return item.stages.filter(status=ItemStage.Status.FAILED).order_by("order").first()


def retry_failed_item(
    item_id: int, wake: WakeWorker = ensure_worker_running
) -> TaskItem:
    """Run the stage that failed again, and carry on from there."""
    item = get_item(item_id)
    check_item_can_retry(item)
    row = failed_stage(item)
    reset_stage_row(row)
    queue_item_at(item, row.stage)
    reopen_task_if_finished(item.task)
    wake()
    return item
