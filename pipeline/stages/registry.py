"""Which function runs each stage."""

from collections.abc import Callable

from pipeline.models import TaskItem
from pipeline.stages.definitions import Stage
from pipeline.stages.handlers.fetch import (
    StageDeps,
    fetch_images,
    fetch_problem,
    fetch_submission,
)
from pipeline.stages.handlers.notes import generate_prefill, review, save_notes
from pipeline.stages.outcomes import StageOutcome
from pipeline.tasks.settings import TaskSettings

StageHandler = Callable[[TaskItem, TaskSettings, StageDeps], StageOutcome]

HANDLERS: dict[Stage, StageHandler] = {
    Stage.FETCH_PROBLEM: fetch_problem,
    Stage.FETCH_IMAGES: fetch_images,
    Stage.FETCH_SUBMISSION: fetch_submission,
    Stage.GENERATE_PREFILL: generate_prefill,
    Stage.REVIEW: review,
    Stage.SAVE_NOTES: save_notes,
}


def handler_for(stage: str) -> StageHandler:
    """The handler for a stage, given as a Stage or its name."""
    return HANDLERS[Stage(stage)]
