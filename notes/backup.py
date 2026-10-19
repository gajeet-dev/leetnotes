"""Backing up a note before it is overwritten."""

import shutil
from datetime import datetime
from pathlib import Path

from django.utils import timezone


def backup_stamp(now: datetime | None = None) -> str:
    """A sortable local-time stamp for file names, like 20261019-153012."""
    return timezone.localtime(now or timezone.now()).strftime("%Y%m%d-%H%M%S")


def backup_path(backup_dir: Path, slug: str, note_path: Path, stamp: str) -> Path:
    """<backup_dir>/<slug>/<note name>-<stamp>.md"""
    return backup_dir / slug / f"{note_path.stem}-{stamp}{note_path.suffix}"


def free_path(path: Path) -> Path:
    """The path itself, or the first '-2', '-3', ... variant that doesn't exist yet."""
    candidate, counter = path, 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}-{counter}{path.suffix}")
        counter += 1
    return candidate


def backup_note(
    note_path: Path, backup_dir: Path, slug: str, stamp: str | None = None
) -> Path:
    """Copy the note into the backup folder and return the copy's path.

    The original is left where it is. Two backups in the same second never
    overwrite each other.
    """
    destination = free_path(
        backup_path(backup_dir, slug, note_path, stamp or backup_stamp())
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(note_path, destination)
    return destination
