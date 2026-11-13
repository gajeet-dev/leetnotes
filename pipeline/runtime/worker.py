"""The background worker: an always-on worker that sleeps when queue is empty and wakes on signal."""

import threading
from contextlib import contextmanager

import structlog
from django.db import connections

from userconfig.paths import worker_lock_path

from ..engine import has_queued_work, step
from ..stages.deps import StageDeps
from .locking import FileLock
from .pacing import Pacer
from .recovery import recover_interrupted

logger = structlog.get_logger(__name__)

CRASH_PAUSE_SECONDS = 0.5  # brief pause after an unexpected error before retrying
HELD_RETRY_SECONDS = 0.1  # short pause when work exists but was held back (e.g. pacing)
MAX_CRASHES = 5  # unexpected errors in a row before giving up


class Worker:
    def __init__(
        self,
        *,
        deps: StageDeps | None = None,
        lock: FileLock | None = None,
    ):
        self.deps = deps or StageDeps()
        self.lock = lock or FileLock(worker_lock_path())
        self.stop_event = threading.Event()
        self.work_available = threading.Event()
        self.pacer = Pacer(wait=self.wait)

    # --- controlling it ---

    def stop(self) -> None:
        """Ask the worker to stop. Unblocks any sleep so it exits promptly."""
        self.stop_event.set()
        self.work_available.set()

    def wake(self) -> None:
        """Signal that work is available to be processed."""
        self.work_available.set()

    def wait(self, seconds: float) -> bool:
        """Wait up to `seconds`. Returns False if the worker was told to stop meanwhile."""
        return not self.stop_event.wait(seconds)

    # --- lock + recovery ---

    def recover_leftovers(self) -> None:
        """Safe here: we hold the lock, so nothing marked running can really be running."""
        if fixed := recover_interrupted():
            logger.info("worker_recovered_interrupted_work", fixed=fixed)

    @contextmanager
    def holding_worker_lock(self):
        """Yield True once we own the lock and have recovered leftovers, else False."""
        if not self.lock.acquire():
            logger.info("worker_not_started", reason="another worker is running")
            yield False
            return
        try:
            self.recover_leftovers()
            yield True
        finally:
            connections.close_all()
            self.lock.release()

    # --- execution ---

    def record_crash_and_should_give_up(self, crashes: int) -> bool:
        """Log the crash being handled, pause briefly, and report whether we've crashed too often."""
        logger.exception("worker_step_crashed", crashes=crashes)
        if crashes >= MAX_CRASHES:
            return True
        self.wait(CRASH_PAUSE_SECONDS)
        return False

    def drain_queue(self) -> bool:
        """Run stages one by one until no queued work remains in active tasks.

        Returns False if stopped early due to repeated crashes.
        """
        crashes = 0
        while not self.stop_event.is_set():
            try:
                result = step(self.deps, before_stage=self.pacer.before_stage)
            except Exception:
                crashes += 1
                if self.record_crash_and_should_give_up(crashes):
                    return False
                continue

            crashes = 0
            if result is not None:
                continue
            if not has_queued_work():
                break
            # Work exists but was held back (e.g. pacing interval)
            self.wait(HELD_RETRY_SECONDS)
        return True

    def run(self) -> bool:
        """Work until idle or stopped (runs once). Returns False if lock not acquired."""
        with self.holding_worker_lock() as acquired:
            if acquired:
                self.drain_queue()
        return acquired

    def run_forever(self) -> bool:
        """Always-on loop: sleeps on signal with 0% CPU when idle, wakes when work arrives."""
        with self.holding_worker_lock() as acquired:
            if not acquired:
                return False

            # Checks for pending subtasks with active tasks
            # if yes the we set the work_available to True
            # so that it doesn't freeze with the first wait
            # inside the while loop and if not it freezes and
            # waits for work_available to be set.
            if has_queued_work():
                self.work_available.set()

            while not self.stop_event.is_set():
                self.work_available.wait()

                # while the worker is waiting if the user
                # stops the server or worker using stop()
                # then it breaks out of this loop after
                # waking from from the wait()
                if self.stop_event.is_set():
                    break

                healthy = self.drain_queue()
                connections.close_all()

                # Worker was busy running stages in self.drain_queue().
                # Server stops. drain_queue() halts, and this check catches it
                # immediately—skipping the database query and exiting cleanly.
                if self.stop_event.is_set():
                    break

                self.work_available.clear()
                if healthy and has_queued_work():
                    self.work_available.set()

        return True


# --- module-level singleton ---

_worker: Worker | None = None
_thread: threading.Thread | None = None
_lock = threading.RLock()


def get_worker() -> Worker:
    global _worker
    with _lock:
        if _worker is None:
            _worker = Worker()
        return _worker


def is_worker_running() -> bool:
    return _thread is not None and _thread.is_alive()


def _spawn_thread(target) -> threading.Thread:
    thread = threading.Thread(target=target, name="pipeline-worker", daemon=True)
    thread.start()
    return thread


def start_worker() -> bool:
    """Start the background worker thread if not already running."""
    global _thread
    with _lock:
        if is_worker_running():
            return False
        _thread = _spawn_thread(get_worker().run_forever)
        return True


def wake_worker() -> bool:
    """Signal the background worker that new work is available in the database."""
    start_worker()
    get_worker().wake()
    return True


ensure_worker_running = wake_worker


def stop_worker() -> None:
    """Stop the background worker."""
    if _worker is not None:
        _worker.stop()


def reset_worker_state() -> None:
    """Reset worker state (primarily used in tests)."""
    global _worker, _thread
    with _lock:
        if _worker is not None:
            _worker.stop()
        _worker = None
        _thread = None
