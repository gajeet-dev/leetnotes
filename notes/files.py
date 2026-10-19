"""Small file helpers shared by the note writers."""

from pathlib import Path


def write_atomic(path: Path, text: str) -> None:
    """Write via a temp file and swap it in, so a crash or a sync client
    (Obsidian Sync, Syncthing) never sees a half-written note."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def has_content(path: Path) -> bool:
    """True if the note exists and isn't blank.

    A blank file is what Obsidian creates when you click a link to a note
    that doesn't exist yet, so it counts as "no note".
    """
    if not path.exists():
        return False
    return bool(path.read_text(encoding="utf-8", errors="ignore").strip())
