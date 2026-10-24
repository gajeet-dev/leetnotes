"""Generate a problem's problem note and solution note in one call."""

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from problems.models import Problem

from .policy import NoteAction
from .services import write_problem_note
from .solution_notes import write_solution_note
from .writer import NoteWriteResult


@dataclass
class ProblemNotesResult:
    slug: str
    problem_note: NoteWriteResult
    solution_note: NoteWriteResult


def generate_notes(
    problem: Problem,
    output_dirs: Sequence[Path | str] | None = None,
    *,
    replace_edited: bool = False,
) -> ProblemNotesResult:
    """Write both notes. The two are independent: one can be skipped while the other is written.

    Unedited notes are refreshed freely. A note you edited is left alone unless
    replace_edited is set, in which case the old version is backed up first.
    """
    return ProblemNotesResult(
        slug=problem.slug,
        problem_note=write_problem_note(
            problem, output_dirs, replace_edited=replace_edited
        ),
        solution_note=write_solution_note(
            problem, output_dirs, replace_edited=replace_edited
        ),
    )


def has_errors(result: ProblemNotesResult) -> bool:
    return bool(result.problem_note.errors or result.solution_note.errors)


def join_counts(**counts: int) -> str:
    """'written 1, failed 2': only the non-zero counts."""
    shown = [f"{label.replace('_', ' ')} {n}" for label, n in counts.items() if n]
    return ", ".join(shown) or "nothing to do"


# --- describing one problem ---

ACTION_COUNT_NAMES = {
    NoteAction.WRITE: "written",
    NoteAction.OVERWRITE: "refreshed",
    NoteAction.BACKUP_AND_OVERWRITE: "replaced",  # the old version was backed up
    NoteAction.UP_TO_DATE: "up_to_date",
    NoteAction.ADOPT: "now_tracked",
    NoteAction.SKIP_EDITED: "kept_edited",
}
COUNT_ORDER = [*ACTION_COUNT_NAMES.values(), "failed"]


def count_outcomes(result: NoteWriteResult) -> Counter:
    """How many output folders ended up in each outcome, for one kind of note."""
    counts: Counter = Counter()
    for outcome in result.outcomes:
        if not outcome.error:
            counts[ACTION_COUNT_NAMES[outcome.action]] += 1
    counts["failed"] = len(result.errors)
    return counts


def describe_note(label: str, result: NoteWriteResult) -> str:
    if result.skipped_reason:
        return f"{label} skipped ({result.skipped_reason})"
    counts = count_outcomes(result)
    return f"{label}: {join_counts(**{name: counts[name] for name in COUNT_ORDER})}"


def describe_problem_note(result: NoteWriteResult) -> str:
    return describe_note("problem note", result)


def describe_solution_note(result: NoteWriteResult) -> str:
    return describe_note("solution note", result)


def describe_result(result: ProblemNotesResult) -> str:
    """One line for the command: 'two-sum: problem note: written 1; solution note: written 1'"""
    return (
        f"{result.slug}: {describe_problem_note(result.problem_note)}; "
        f"{describe_solution_note(result.solution_note)}"
    )


# --- describing a whole run ---


@dataclass
class RunTotals:
    problems: int = 0
    counts: Counter = field(
        default_factory=Counter
    )  # outcomes across both kinds of note
    skipped: int = 0  # problems where at least one note was skipped
    failed: int = 0  # problems with at least one write error


def tally(results: Iterable[ProblemNotesResult]) -> RunTotals:
    totals = RunTotals()
    for result in results:
        totals.problems += 1
        for note in (result.problem_note, result.solution_note):
            totals.counts.update(count_outcomes(note))
        totals.skipped += bool(
            result.problem_note.skipped_reason or result.solution_note.skipped_reason
        )
        totals.failed += has_errors(result)
    return totals


def describe_totals(totals: RunTotals) -> str:
    note_counts = {
        name: totals.counts[name] for name in COUNT_ORDER if name != "failed"
    }
    counts = join_counts(
        **note_counts,
        problems_with_a_skipped_note=totals.skipped,
        failed=totals.failed,
    )
    return f"Done: {totals.problems} problem(s): {counts}."


def error_details(result: ProblemNotesResult) -> list[str]:
    """One indented line per write error, as '  <folder>: <message>'."""
    errors = [*result.problem_note.errors.items(), *result.solution_note.errors.items()]
    return [f"  {folder}: {message}" for folder, message in errors]
