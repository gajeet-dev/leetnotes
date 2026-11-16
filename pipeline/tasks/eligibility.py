"""Who can be put into the pipeline."""

from collections.abc import Sequence
from dataclasses import dataclass

from pipeline.models import PipelineItem
from problems.models import Problem

BUSY_ITEM_STATUSES = (
    PipelineItem.Status.QUEUED,
    PipelineItem.Status.RUNNING,
    PipelineItem.Status.NEEDS_INPUT,
)


@dataclass(frozen=True)
class Rejection:
    problem: Problem
    reason: str


def busy_problem_ids(problems: Sequence[Problem]) -> set[int]:
    """Problems that are currently active and unfinished in the pipeline."""
    return set(
        PipelineItem.objects.filter(
            problem__in=list(problems),
            status__in=BUSY_ITEM_STATUSES,
        ).values_list("problem_id", flat=True)
    )


def rejection_reason(problem: Problem, busy_ids: set[int]) -> str | None:
    """Why this problem can't run right now, or None if it can."""
    if not problem.solved:
        return "not solved yet"
    if problem.leetcode_id is None:
        return "not fully discovered yet (no LeetCode id)"
    if problem.paid_only and not problem.content_remote_markdown:
        return "premium problem: its statement isn't available"
    if problem.pk in busy_ids:
        return "already running in the pipeline"
    return None


def split_eligible(
    problems: Sequence[Problem],
) -> tuple[list[Problem], list[Rejection]]:
    """Separate the problems that can run from the ones that can't, keeping the order."""
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
