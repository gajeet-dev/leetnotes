from prefill.models import AIResponse
from prefill.schema import SolutionNote
from problems.models import Problem

from .naming import problem_note_stem, solution_note_stem


def build_problem_context(problem: Problem) -> dict:
    """Map a Problem to the variable names problem.md.j2 expects."""
    return {
        "frontend_id": problem.leetcode_id,
        "slug": problem.slug,
        "title": problem.title,
        "difficulty": problem.difficulty,
        "url": problem.url,
        "tags": problem.tags or [],  # list of {"name", "slug"} dicts
        "content": problem.content_local_markdown or "",
        "submission": problem.submissions.order_by("-submission_date").first(),
        "solution_note": solution_note_stem(problem),
    }


def active_solution_note(problem: Problem) -> SolutionNote | None:
    """The validated AI note currently marked active for this problem, if any."""
    response = AIResponse.objects.filter(problem=problem, is_active=True).first()
    if response is None or not response.parsed:
        return None
    # Re-validating fills in defaults, so older stored responses survive schema changes.
    return SolutionNote.model_validate(response.parsed)


def tag_slugs(problem: Problem) -> list[str]:
    return [tag["slug"] for tag in problem.tags or [] if tag.get("slug")]


def problem_header(problem: Problem) -> dict:
    """The front matter and title values that come from the problem itself."""
    return {
        "frontend_id": problem.leetcode_id,
        "slug": problem.slug,
        "title": problem.title,
        "difficulty": problem.difficulty,
        "url": problem.url,
        "tags": tag_slugs(problem),
        "problem_note": problem_note_stem(problem),  # target of the [[link]] back
    }


def note_fields(note: SolutionNote) -> dict:
    """The body values that come from the AI note."""
    data = note.model_dump()
    return {
        "problem_summary": data["problem_summary"],
        "recognition_clue": data["recognition_clue"],
        "approaches": data["approaches"],
        "takeaway": data["takeaway"],
    }


def hand_written_fields() -> dict:
    """Fields the AI doesn't fill: empty now, for you to edit in Obsidian."""
    return {"aliases": [], "related": [], "flashcards": []}


def build_solution_context(problem: Problem, note: SolutionNote) -> dict:
    return {
        **problem_header(problem),
        **note_fields(note),
        **hand_written_fields(),
    }
