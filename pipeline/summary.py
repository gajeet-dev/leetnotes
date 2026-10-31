"""Short plain-text summaries of tasks, for the command line."""

from collections import Counter

from .models import Task, TaskItem


def recent_tasks(limit: int = 5) -> list[Task]:
    return list(Task.objects.order_by("-created_at")[:limit])


def item_counts(task: Task) -> Counter:
    return Counter(task.items.values_list("status", flat=True))


def describe_counts(counts: Counter) -> str:
    parts = [
        f"{counts[status]} {status.value.replace('_', ' ')}"
        for status in TaskItem.Status
        if counts[status]
    ]
    return ", ".join(parts) or "no items"


def describe_task(task: Task) -> str:
    line = f"{task.name} [{task.status}]: {describe_counts(item_counts(task))}"
    return f"{line} ({task.pause_reason})" if task.pause_reason else line


def describe_recent_tasks(limit: int = 5) -> list[str]:
    return [describe_task(task) for task in recent_tasks(limit)]
