"""The background worker: runs queued tasks, one stage at a time, until nothing is left."""

import functools
import threading
import time
from collections.abc import Callable
from enum import StrEnum

import structlog
from django.db import connections

from userconfig.paths import worker_lock_path

from .engine import has_queued_work, step
from .handlers import StageDeps
from .locking import FileLock
from .pacing import Pacer
from .recovery import recover_interrupted

logger = structlog.get_logger(__name__)

POLL_SECONDS = 2.0  # how often an idle worker looks for new work
HELD_RETRY_SECONDS = 0.1  # a short pause when work exists but wasn't run this time
DEFAULT_IDLE_TIMEOUT = (
    60.0  # a background worker exits after this long with nothing to do
)
MAX_CRASHES = 5  # unexpected errors in a row before the worker gives up


class Move(StrEnum):
    WORKED = "worked"  # a stage ran
    HELD = "held"  # work exists but wasn't run this time (paused, stopped or changed meanwhile)
    IDLE = "idle"  # nothing to do


class Worker:
    def __init__(
        self,
        *,
        idle_timeout: float | None = DEFAULT_IDLE_TIMEOUT,  # None: never exit when idle
        poll_seconds: float = POLL_SECONDS,
        deps: StageDeps | None = None,
        lock: FileLock | None = None,
    ):
        self.idle_timeout = idle_timeout
        self.poll_seconds = poll_seconds
        self.deps = deps or StageDeps()
        self.lock = lock or FileLock(worker_lock_path())
        self.stop_event = threading.Event()
        self.pacer = Pacer(wait=self.wait)

    # --- controlling it ---

    def stop(self) -> None:
        """Ask the worker to stop. It finishes the stage it is in first."""
        self.stop_event.set()

    def wait(self, seconds: float) -> bool:
        """Wait up to `seconds`. Returns False if the worker was told to stop meanwhile."""
        return not self.stop_event.wait(seconds)

    # --- running ---

    def run(self) -> bool:
        """Work until idle or stopped. Returns False if another worker already holds the lock."""
        if not self.lock.acquire():
            logger.info("worker_not_started", reason="another worker is running")
            return False
        try:
            self.recover_leftovers()
            self.work_until_idle()
        finally:
            self.lock.release()
        return True

    def recover_leftovers(self) -> None:
        """Safe here: we hold the lock, so nothing marked running can really be running."""
        if fixed := recover_interrupted():
            logger.info("worker_recovered_interrupted_work", fixed=fixed)

    def work_until_idle(self) -> None:
        idle_since: float | None = None
        crashes = 0
        while not self.stop_event.is_set():
            try:
                move = self.next_move()
            except Exception:  # keep going: a stage's own errors never reach here
                crashes += 1
                logger.exception("worker_step_crashed", crashes=crashes)
                if crashes >= MAX_CRASHES:
                    return
                self.wait(self.poll_seconds)
                continue
            crashes = 0
            if move is Move.WORKED:
                idle_since = None
            elif move is Move.HELD:
                self.wait(HELD_RETRY_SECONDS)
            else:
                idle_since = time.monotonic() if idle_since is None else idle_since
                if self.idle_for_too_long(idle_since):
                    return
                self.wait(self.poll_seconds)

    def next_move(self) -> Move:
        if step(self.deps, before_stage=self.pacer.before_stage) is not None:
            return Move.WORKED
        return Move.HELD if has_queued_work() else Move.IDLE

    def idle_for_too_long(self, idle_since: float) -> bool:
        return (
            self.idle_timeout is not None
            and time.monotonic() - idle_since >= self.idle_timeout
        )


def spawn_daemon_thread(target: Callable[[], None]) -> None:
    """Run `target` in a background thread, and close its database connections when it ends."""

    def run() -> None:
        try:
            target()
        finally:
            connections.close_all()

    threading.Thread(target=run, name="pipeline-worker", daemon=True).start()


class WorkerManager:
    """Starts the background worker when there is work, and makes sure only one runs here."""

    def __init__(
        self,
        spawn: Callable[[Callable[[], None]], None] = spawn_daemon_thread,
        make_worker: Callable[[], Worker] = Worker,
    ):
        self._mutex = threading.Lock()
        self._worker: Worker | None = None
        self._spawn = spawn
        self._make_worker = make_worker

    @property
    def is_running(self) -> bool:
        return self._worker is not None

    def ensure_running(self) -> bool:
        """Start the worker if none is running. Returns True if one was started."""
        with self._mutex:
            if self._worker is not None:
                return False
            worker = self._worker = self._make_worker()
        self._spawn(functools.partial(self._run, worker))
        return True

    def stop(self) -> None:
        with self._mutex:
            worker = self._worker
        if worker is not None:
            worker.stop()

    def _run(self, worker: Worker) -> None:
        try:
            self._work_until_nothing_is_left(worker)
        finally:
            with self._mutex:
                if self._worker is worker:
                    self._worker = None

    def _work_until_nothing_is_left(self, worker: Worker) -> None:
        while True:
            owned = worker.run()
            with self._mutex:
                # Decided under the same mutex that ensure_running uses, so work that
                # arrives as the worker finishes is either seen here or starts a new worker.
                if not owned or worker.stop_event.is_set() or not has_queued_work():
                    self._worker = None
                    return


manager = WorkerManager()


def ensure_worker_running() -> bool:
    return manager.ensure_running()


def stop_worker() -> None:
    manager.stop()
