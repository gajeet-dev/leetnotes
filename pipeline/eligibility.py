"""Who can be put into a task."""

from collections.abc import Sequence
from dataclasses import dataclass

from problems.models import Problem

from .models import Task, TaskItem

BUSY_ITEM_STATUSES = (
    TaskItem.Status.QUEUED,
    TaskItem.Status.RUNNING,
    TaskItem.Status.NEEDS_INPUT,
)
OPEN_TASK_STATUSES = (Task.Status.ACTIVE, Task.Status.PAUSED)


@dataclass(frozen=True)
class Rejection:
    problem: Problem
    reason: str


def busy_problem_ids(problems: Sequence[Problem]) -> set[int]:
    """Problems that are unfinished in another open task."""
    return set(
        TaskItem.objects.filter(
            problem__in=list(problems),
            status__in=BUSY_ITEM_STATUSES,
            task__status__in=OPEN_TASK_STATUSES,
        ).values_list("problem_id", flat=True)
    )


def rejection_reason(problem: Problem, busy_ids: set[int]) -> str | None:
    """Why this problem can't join a task, or None if it can."""
    if not problem.solved:
        return "not solved yet"
    if problem.leetcode_id is None:
        return "not fully discovered yet (no LeetCode id)"
    if problem.paid_only and not problem.content_remote_markdown:
        return "premium problem: its statement isn't available"
    if problem.pk in busy_ids:
        return "already in a running task"
    return None


def split_eligible(
    problems: Sequence[Problem],
) -> tuple[list[Problem], list[Rejection]]:
    """Separate the problems that can join from the ones that can't, keeping the order."""
    busy_ids = busy_problem_ids(problems)
    eligible: list[Problem] = []
    rejected: list[Rejection] = []
    for problem in problems:
        reason = rejection_reason(problem, busy_ids)
        if reason:
            rejected.append(Rejection(problem, reason))
        else:
            eligible.append(problem)
    return eligible, rejected
