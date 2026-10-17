import threading
from collections.abc import Sequence

import structlog
from django.db import connections

from leetcode.bootstrap import get_client, get_image_downloader

from .sync import process_pending

logger = structlog.get_logger(__name__)

# One run at a time, so two clicks can't overlap. The rate limiter is shared
# anyway, so a second concurrent run would gain nothing.
_run_lock = threading.Lock()


def start_background_sync(
    problem_ids: Sequence[int], *, retry_failed: bool = False
) -> bool:
    """Start a sync thread. Returns False if one is already running."""
    if not _run_lock.acquire(blocking=False):
        return False

    def _target() -> None:
        try:
            process_pending(
                get_client(),
                get_image_downloader(),
                problem_ids=list(problem_ids),
                retry_failed=retry_failed,
            )
        except Exception:
            logger.exception("background_sync_crashed")
        finally:
            connections.close_all()  # threads own their own DB connections
            _run_lock.release()

    threading.Thread(target=_target, name="sync-run", daemon=True).start()
    return True
