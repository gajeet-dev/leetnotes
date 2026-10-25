import os
from pathlib import Path


def write_text_atomic(path: Path, text: str, *, mode: int = 0o644) -> None:
    """Write via a temp file and swap it in, so a crash never leaves a half-written file.

    The temp file is created with `mode` from the start (use 0o600 for secrets),
    so it is never briefly readable by others.
    """
    tmp = path.with_name(path.name + ".tmp")
    descriptor = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)
    tmp.replace(path)
