"""Is a note on disk still what we wrote? Compares the file with its record."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from problems.models import Problem

from .files import has_content
from .hashing import file_hash
from .layout import layout_for
from .models import NoteRecord
from .naming import problem_note_filename, solution_note_filename


class NoteState(StrEnum):
    NEVER_WRITTEN = "never_written"    # no record and no file
    UNCHANGED = "unchanged"            # the file is exactly what we wrote
    MODIFIED = "modified"              # the file differs from what we wrote: you edited it
    MISSING = "missing"                # we wrote it once, but the file is gone
    UNTRACKED_FILE = "untracked_file"  # a file is there but we have no record (older notes)


@dataclass(frozen=True)
class NoteInspection:
    path: Path
    record: NoteRecord | None
    state: NoteState


def dir_key(output_dir: Path | str) -> str:
    """How an output folder is stored on records."""
    return str(Path(output_dir).expanduser())


def expected_path(problem: Problem, kind: str, output_dir: Path | str) -> Path:
    layout = layout_for(Path(output_dir).expanduser())
    if kind == NoteRecord.Kind.PROBLEM:
        return layout.problems_dir / problem_note_filename(problem)
    return layout.solutions_dir / solution_note_filename(problem)


def find_record(problem: Problem, kind: str, output_dir: Path | str) -> NoteRecord | None:
    return NoteRecord.objects.filter(
        problem=problem, kind=kind, output_dir=dir_key(output_dir)
    ).first()


def classify_note(recorded_hash: str | None, path: Path) -> NoteState:
    """The state of the file at `path`, given the hash we recorded (or None)."""
    on_disk = has_content(path)  # a blank file counts as no file
    if recorded_hash is None:
        return NoteState.UNTRACKED_FILE if on_disk else NoteState.NEVER_WRITTEN
    if not on_disk:
        return NoteState.MISSING
    if file_hash(path) == recorded_hash:
        return NoteState.UNCHANGED
    return NoteState.MODIFIED


def inspect_note(problem: Problem, kind: str, output_dir: Path | str) -> NoteInspection:
    path = expected_path(problem, kind, output_dir)
    record = find_record(problem, kind, output_dir)
    state = classify_note(record.content_hash if record else None, path)
    return NoteInspection(path=path, record=record, state=state)
