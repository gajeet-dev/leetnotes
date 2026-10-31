"""What you can do to a task: start it, pause it, resume it, cancel it."""

from collections.abc import Callable, Iterable

from problems.models import Problem

from .creation import TaskCreation, create_task
from .models import Task
from .transitions import cancel_task, pause_task, resume_task
from .worker import ensure_worker_running

WakeWorker = Callable[[], bool]


def get_task(task_id: int) -> Task:
    return Task.objects.get(pk=task_id)


def start_task(
    problems: Iterable[Problem],
    overrides: dict | None = None,
    *,
    name: str = "",
    wake: WakeWorker = ensure_worker_running,
) -> TaskCreation:
    """Create a task and make sure a worker is running to carry it out."""
    creation = create_task(problems, overrides, name=name)
    wake()
    return creation


def pause(task_id: int) -> Task:
    """Stop picking up this task's items. A stage already running finishes first."""
    task = get_task(task_id)
    pause_task(task)
    return task


def resume(task_id: int, wake: WakeWorker = ensure_worker_running) -> Task:
    task = get_task(task_id)
    resume_task(task)
    wake()
    return task


def cancel(task_id: int) -> Task:
    """Stop the task for good. Its problems become available for other tasks."""
    task = get_task(task_id)
    cancel_task(task)
    return task
