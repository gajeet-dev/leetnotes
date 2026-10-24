"""Plan a note write: inspect the file, then apply the policy."""

from dataclasses import dataclass
from pathlib import Path

from problems.models import Problem

from .files import has_content
from .hashing import content_hash, file_hash
from .models import NoteRecord
from .policy import NoteAction, decide_action
from .state import NoteState, inspect_note


@dataclass(frozen=True)
class WritePlan:
    action: NoteAction
    state: NoteState
    path: Path
    record: NoteRecord | None


def text_matches_file(path: Path, new_text: str) -> bool:
    """True if the file already contains this text (ignoring trivial differences)."""
    return has_content(path) and file_hash(path) == content_hash(new_text)


def plan_write(
    problem: Problem,
    kind: str,
    output_dir: Path | str,
    new_text: str,
    *,
    replace_edited: bool = False,
) -> WritePlan:
    inspection = inspect_note(problem, kind, output_dir)
    action = decide_action(
        inspection.state,
        replace_edited=replace_edited,
        text_matches_file=text_matches_file(inspection.path, new_text),
    )
    return WritePlan(action, inspection.state, inspection.path, inspection.record)
