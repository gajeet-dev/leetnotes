"""What a stage reports back to the engine."""

from dataclasses import dataclass

from .models import ItemStage

DETAIL_LIMIT = 300  # the size of the ItemStage.detail column


class StageError(Exception):
    """The stage failed for this problem. str(exc) is the message shown to you."""


class PauseTask(Exception):
    """Nothing more in this task can run until something is fixed. str(exc) says what."""


@dataclass(frozen=True)
class StageOutcome:
    status: str  # ItemStage.Status: DONE, WAITING or SKIPPED
    detail: str = ""
    input_kind: str = ""  # what the item is waiting for, when WAITING


def clip(text: str, limit: int = DETAIL_LIMIT) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def done(detail: str = "") -> StageOutcome:
    return StageOutcome(ItemStage.Status.DONE, clip(detail))


def skipped(detail: str) -> StageOutcome:
    return StageOutcome(ItemStage.Status.SKIPPED, clip(detail))


def waiting(input_kind: str, detail: str = "") -> StageOutcome:
    return StageOutcome(ItemStage.Status.WAITING, clip(detail), input_kind)
