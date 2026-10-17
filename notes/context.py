from problems.models import Problem


def build_problem_context(problem: Problem) -> dict:
    """Map a Problem to the variable names problem.md.j2 expects."""
    return {
        "frontend_id": problem.leetcode_id,
        "slug": problem.slug,
        "title": problem.title,
        "difficulty": problem.difficulty,
        "url": problem.url,
        "tags": problem.tags or [],  # list of {"name", "slug"} dicts
        "content": problem.content_local_markdown,
        "submission": problem.submissions.order_by("-submission_date").first(),
    }
