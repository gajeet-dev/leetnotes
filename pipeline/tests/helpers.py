from problems.models import Problem, SyncTask

from ..models import PipelineItem, TaskItem


def make_problem(slug: str, leetcode_id: int, **overrides) -> Problem:
    values = {"solved": True, "title": slug, "content_remote_markdown": "Statement."}
    return Problem.objects.create(
        slug=slug, leetcode_id=leetcode_id, **{**values, **overrides}
    )


def mark_fetched(problem: Problem, *parts: str) -> None:
    for part in parts or SyncTask.Part.values:
        SyncTask.objects.update_or_create(
            problem=problem, part=part, defaults={"status": SyncTask.Status.DONE}
        )


def make_item(problem: Problem) -> PipelineItem:
    return PipelineItem.objects.create(problem=problem)
