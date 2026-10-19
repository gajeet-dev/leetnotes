"""Small file helpers shared by the note writers."""

from pathlib import Path


def write_atomic(path: Path, text: str) -> None:
    """Write via a temp file and swap it in, so a crash or a sync client
    (Obsidian Sync, Syncthing) never sees a half-written note."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
