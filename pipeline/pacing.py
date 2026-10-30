"""Spacing the work out so LeetCode isn't hit in bursts."""

import random
import time
from collections.abc import Callable

from userconfig import effective

from .models import ItemStage, TaskItem
from .stages import Stage

PACED_STAGES = {Stage.FETCH_PROBLEM, Stage.FETCH_IMAGES, Stage.FETCH_SUBMISSION}
JITTER_FRACTION = 0.2  # up to 20% extra, so the rhythm isn't perfectly regular


def seconds_per_problem() -> float | None:
    """How long each problem should take at the saved pace, or None for no pacing."""
    pace = effective.problems_per_minute()
    return 60.0 / pace if pace else None


def random_jitter(interval: float) -> float:
    return random.uniform(0, interval * JITTER_FRACTION)


class Pacer:
    """Makes fetching a new problem start at least one interval after the previous one started.

    wait(seconds) waits that long and returns False if the worker was told to stop meanwhile.
    """

    def __init__(
        self,
        interval: Callable[[], float | None] = seconds_per_problem,
        *,
        wait: Callable[[float], bool],
        clock: Callable[[], float] = time.monotonic,
        jitter: Callable[[float], float] = random_jitter,
    ):
        self.interval = interval
        self.wait = wait
        self.clock = clock
        self.jitter = jitter
        self._last_item_id: int | None = None
        self._last_start: float | None = None

    def before_stage(self, item: TaskItem, row: ItemStage) -> bool:
        """The engine's hook: returns False if the worker was stopped while waiting."""
        if row.stage not in PACED_STAGES or item.pk == self._last_item_id:
            return True
        return self.take_turn(item.pk)

    def take_turn(self, item_id: int) -> bool:
        """Wait until it's this problem's turn to start fetching, then claim the turn."""
        gap = self.interval()
        if gap and self._last_start is not None:
            remaining = self._last_start + gap + self.jitter(gap) - self.clock()
            if remaining > 0 and not self.wait(remaining):
                return False  # stopped while waiting: the turn isn't claimed
        self._last_item_id = item_id
        self._last_start = self.clock()
        return True
