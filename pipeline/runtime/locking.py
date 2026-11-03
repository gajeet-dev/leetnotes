"""A lock that only one holder (a process, or a thread of one) can have at a time."""

import fcntl
from pathlib import Path


class FileLock:
    """An exclusive lock on a file. The operating system drops it if the process dies.

    Each FileLock opens the file itself, so two locks on the same path also keep each
    other out inside one process. (Linux and macOS only, like the rest of the app.)
    """

    def __init__(self, path: Path):
        self.path = path
        self._handle = None

    def acquire(self) -> bool:
        """Take the lock if nobody else holds it. Never waits."""
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        handle = open(self.path, "a+")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle is not None:
            fcntl.flock(self._handle, fcntl.LOCK_UN)
            self._handle.close()
            self._handle = None
