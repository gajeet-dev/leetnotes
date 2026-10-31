"""Solution notes: the AI-drafted study note for a solved problem."""

from collections.abc import Sequence
from pathlib import Path

import structlog

from prefill.schema import SolutionNote
from problems.models import Problem

from .context import active_solution_note, build_solution_context
from .models import NoteRecord
from .rendering import render_template
from .writer import NoteWriteResult, write_to_folders

logger = structlog.get_logger(__name__)

TEMPLATE_NAME = "solution.md.j2"


class SolutionNoteUnavailable(Exception):
    """The problem isn't ready for a solution note. str(exc) says why."""


def require_solution_note(problem: Problem) -> SolutionNote:
    """The problem's active AI note, or SolutionNoteUnavailable with the reason."""
    if problem.leetcode_id is None:
        raise SolutionNoteUnavailable("no LeetCode id yet")
    note = active_solution_note(problem)
    if note is None:
        raise SolutionNoteUnavailable("no active AI note")
    return note


def render_solution_note(problem: Problem) -> str:
    note = require_solution_note(problem)
    return render_template(TEMPLATE_NAME, build_solution_context(problem, note))


def write_solution_note(
    problem: Problem,
    output_dirs: Sequence[Path | str] | None = None,
    *,
    replace_edited: bool = False,
) -> NoteWriteResult:
    """Render the solution note once and write it into every output folder."""
    try:
        text = render_solution_note(problem)  # render first: a failure writes nothing
    except SolutionNoteUnavailable as exc:
        return NoteWriteResult(
            problem.slug, NoteRecord.Kind.SOLUTION, skipped_reason=str(exc)
        )
    return write_to_folders(
        problem,
        NoteRecord.Kind.SOLUTION,
        text,
        output_dirs,
        replace_edited=replace_edited,
    )
