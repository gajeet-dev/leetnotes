"""The stages every problem goes through in a task, in order."""

from django.db import models


class Stage(models.TextChoices):
    # The label is the text shown in the "working on" column.
    FETCH_PROBLEM = "fetch_problem", "Fetching problem"
    FETCH_IMAGES = "fetch_images", "Fetching images"
    FETCH_SUBMISSION = "fetch_submission", "Fetching submission"
    GENERATE_PREFILL = "generate_prefill", "Generating prefill"
    REVIEW = "review", "Waiting for review"
    SAVE_NOTES = "save_notes", "Saving notes"


STAGE_ORDER: tuple[Stage, ...] = tuple(Stage)


def stage_order(stage: str) -> int:
    """Where a stage sits in the pipeline: 0 for the first."""
    return [candidate.value for candidate in STAGE_ORDER].index(stage)


def next_stage(stage: str) -> Stage | None:
    """The stage after this one, or None after the last."""
    position = stage_order(stage) + 1
    return STAGE_ORDER[position] if position < len(STAGE_ORDER) else None
