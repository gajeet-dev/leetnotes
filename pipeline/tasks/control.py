"""What you can do to pipeline items: start, pause, resume, cancel."""

from collections.abc import Callable, Iterable

from pipeline.models import PipelineItem
from pipeline.runtime.worker import ensure_worker_running
from problems.models import Problem

from .creation import PipelineStart, start_pipeline_items
from .transitions import cancel_item, pause_item, resume_item

WakeWorker = Callable[[], bool]


def get_item(item_id: int) -> PipelineItem:
    return PipelineItem.objects.get(pk=item_id)


def start_pipeline(
    problems: Iterable[Problem],
    overrides: dict | None = None,
    wake: WakeWorker = ensure_worker_running,
    allow_busy: bool = False,
) -> PipelineStart:
    """Start pipeline for problems and wake worker."""
    return start_pipeline_items(
        problems, overrides=overrides, wake=wake, allow_busy=allow_busy
    )


start_task = start_pipeline  # backward compatibility alias


def pause(item_id: int) -> PipelineItem:
    item = get_item(item_id)
    pause_item(item)
    return item


def resume(item_id: int, wake: WakeWorker = ensure_worker_running) -> PipelineItem:
    item = get_item(item_id)
    resume_item(item)
    wake()
    return item


def cancel(item_id: int) -> PipelineItem:
    item = get_item(item_id)
    cancel_item(item)
    return item
