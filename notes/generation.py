"""Generate a problem's problem note and solution note in one call."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Iterable, Sequence
from problems.models import Problem

from .services import NoteResult, write_problem_note
from .solution_notes import SolutionWriteResult, write_solution_note


@dataclass
class ProblemNotesResult:
    slug: str
    problem_note: NoteResult
    solution_note: SolutionWriteResult


def generate_notes(
    problem: Problem,
    output_dirs: Sequence[Path | str] | None = None,
    *,
    overwrite_solution: bool = False,
) -> ProblemNotesResult:
    """Write both notes. The two are independent: one can be skipped while the other is written.

    The problem note is always regenerated (it is derived from the database).
    The solution note is create-only unless overwrite_solution is set.
    """
    return ProblemNotesResult(
        slug=problem.slug,
        problem_note=write_problem_note(problem, output_dirs),
        solution_note=write_solution_note(
            problem, output_dirs, overwrite=overwrite_solution
        ),
    )


def has_errors(result: ProblemNotesResult) -> bool:
    return bool(result.problem_note.errors or result.solution_note.errors)


def join_counts(**counts: int) -> str:
    """'written 1, failed 2': only the non-zero counts."""
    shown = [f"{label.replace('_', ' ')} {n}" for label, n in counts.items() if n]
    return ", ".join(shown) or "nothing to do"


def describe_problem_note(result: NoteResult) -> str:
    if result.skipped_reason:
        return f"problem note skipped ({result.skipped_reason})"
    counts = join_counts(written=len(result.paths), failed=len(result.errors))
    return f"problem note: {counts}"


def describe_solution_note(result: SolutionWriteResult) -> str:
    if result.skipped_reason:
        return f"solution note skipped ({result.skipped_reason})"
    counts = join_counts(
        written=len(result.written),
        kept_existing=len(result.existing),
        backed_up=len(result.backups),
        failed=len(result.errors),
    )
    return f"solution note: {counts}"


def describe_result(result: ProblemNotesResult) -> str:
    """One line for the command and the admin: 'two-sum: problem note: written 1; ...'"""
    return (
        f"{result.slug}: {describe_problem_note(result.problem_note)}; "
        f"{describe_solution_note(result.solution_note)}"
    )


@dataclass
class RunTotals:
    problems: int = 0
    problem_notes_written: int = 0
    solution_notes_written: int = 0
    solution_notes_kept: int = 0
    backups: int = 0
    skipped: int = 0  # problems where at least one note was skipped
    failed: int = 0  # problems with at least one write error


def tally(results: Iterable[ProblemNotesResult]) -> RunTotals:
    totals = RunTotals()
    for result in results:
        totals.problems += 1
        totals.problem_notes_written += len(result.problem_note.paths)
        totals.solution_notes_written += len(result.solution_note.written)
        totals.solution_notes_kept += len(result.solution_note.existing)
        totals.backups += len(result.solution_note.backups)
        totals.skipped += bool(
            result.problem_note.skipped_reason or result.solution_note.skipped_reason
        )
        totals.failed += has_errors(result)
    return totals


def describe_totals(totals: RunTotals) -> str:
    counts = join_counts(
        problem_notes_written=totals.problem_notes_written,
        solution_notes_written=totals.solution_notes_written,
        solution_notes_kept=totals.solution_notes_kept,
        backed_up=totals.backups,
        problems_with_a_skipped_note=totals.skipped,
        failed=totals.failed,
    )
    return f"Done: {totals.problems} problem(s): {counts}."


def error_details(result: ProblemNotesResult) -> list[str]:
    """One indented line per write error, as '  <folder>: <message>'."""
    errors = [*result.problem_note.errors.items(), *result.solution_note.errors.items()]
    return [f"  {folder}: {message}" for folder, message in errors]
