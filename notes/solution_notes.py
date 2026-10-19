"""Solution notes: the AI-drafted study note for a solved problem."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from prefill.schema import SolutionNote
from problems.models import Problem

from .backup import backup_note
from .context import active_solution_note, build_solution_context
from .files import has_content, write_atomic
from .layout import ensure_layout, resolve_output_dirs
from .naming import solution_note_filename
from .rendering import render_template

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


@dataclass
class SolutionWriteResult:
    slug: str
    written: list[Path] = field(default_factory=list)
    existing: list[Path] = field(default_factory=list)  # already there: left untouched
    backups: list[Path] = field(
        default_factory=list
    )  # old notes copied aside before overwriting
    errors: dict[str, str] = field(default_factory=dict)  # output folder -> error
    skipped_reason: str | None = None


def write_solution_note(
    problem: Problem,
    output_dirs: Sequence[Path | str] | None = None,
    *,
    overwrite: bool = False,
) -> SolutionWriteResult:
    """Render the solution note once and write it into every output folder.

    An existing note is left alone unless overwrite=True, in which case it is
    backed up first.
    """
    result = SolutionWriteResult(problem.slug)
    try:
        text = render_solution_note(problem)  # render first: a failure writes nothing
    except SolutionNoteUnavailable as exc:
        result.skipped_reason = str(exc)
        return result

    for output_dir in resolve_output_dirs(output_dirs):
        write_into_folder(problem, text, output_dir, result, overwrite)
    return result


def write_into_folder(
    problem: Problem,
    text: str,
    output_dir: Path,
    result: SolutionWriteResult,
    overwrite: bool,
) -> None:
    """Write the note into one output folder and record what happened."""
    try:
        layout = ensure_layout(output_dir)
        path = layout.solutions_dir / solution_note_filename(problem)
        if has_content(path):
            if not overwrite:
                result.existing.append(path)  # your edits stay safe
                return
            # Back up first: if this copy fails, the note is never touched.
            result.backups.append(backup_note(path, layout.backup_dir, problem.slug))
        write_atomic(path, text)
    except OSError as exc:
        logger.warning(
            "solution_note_write_failed",
            slug=problem.slug,
            dir=str(output_dir),
            error=str(exc),
        )
        result.errors[str(output_dir)] = str(exc)
        return

    logger.info("solution_note_written", slug=problem.slug, path=str(path))
    result.written.append(path)
