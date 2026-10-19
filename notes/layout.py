"""Where each kind of note lives inside an output folder."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

ROOT_FOLDER = "Leetcode"
PROBLEMS_FOLDER = "problems"  # problem notes, with assets/ inside
SOLUTIONS_FOLDER = "solutions"  # solution notes


@dataclass(frozen=True)
class NotesLayout:
    root: Path
    problems_dir: Path
    solutions_dir: Path


def layout_for(output_dir: Path) -> NotesLayout:
    """The folder paths for an output folder. Creates nothing."""
    root = output_dir / ROOT_FOLDER
    return NotesLayout(
        root=root,
        problems_dir=root / PROBLEMS_FOLDER,
        solutions_dir=root / SOLUTIONS_FOLDER,
    )


def ensure_layout(output_dir: Path) -> NotesLayout:
    """Create the Leetcode folder tree inside output_dir.

    output_dir itself must already exist: a missing or unmounted drive is an
    error, not something to quietly create.
    """
    if not output_dir.is_dir():
        raise FileNotFoundError(f"output folder does not exist: {output_dir}")
    layout = layout_for(output_dir)
    layout.problems_dir.mkdir(parents=True, exist_ok=True)
    layout.solutions_dir.mkdir(parents=True, exist_ok=True)
    return layout


def resolve_output_dirs(output_dirs: Sequence[Path | str] | None = None) -> list[Path]:
    """The folders to write into: the given ones, or NOTES_OUTPUT_DIRS. No duplicates."""
    chosen = settings.NOTES_OUTPUT_DIRS if output_dirs is None else output_dirs
    return list(dict.fromkeys(Path(d).expanduser() for d in chosen))
