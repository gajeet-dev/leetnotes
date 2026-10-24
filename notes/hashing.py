"""Fingerprints of note text, used to tell whether you edited a note."""

import hashlib
from pathlib import Path


def normalize_note_text(text: str) -> str:
    """Make trivial differences not count as edits.

    Line endings, trailing spaces and blank lines at the very start or end
    are ignored, so an editor or sync tool re-saving a file doesn't look
    like a change.
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines).strip("\n")


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize_note_text(text).encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    return content_hash(path.read_text(encoding="utf-8", errors="replace"))
