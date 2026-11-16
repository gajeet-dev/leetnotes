from typing import TYPE_CHECKING

from django.db import models

from problems.models import Problem

from .stages.definitions import Stage

if TYPE_CHECKING:
    from django.db.models.manager import RelatedManager


class PipelineItem(models.Model):
    """One problem in the generation pipeline."""

    class Status(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        NEEDS_INPUT = "needs_input"  # waiting for user action: see input_kind
        PAUSED = "paused"  # paused by user or account-level error
        COMPLETED = "completed"
        FAILED = "failed"
        CANCELLED = "cancelled"

    class InputKind(models.TextChoices):
        MANUAL_PREFILL = "manual_prefill"  # paste a reply from a web chat
        REVIEW = "review"  # approve the AI note before it is saved

    problem = models.ForeignKey(
        Problem, on_delete=models.CASCADE, related_name="pipeline_items"
    )
    position = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.QUEUED, db_index=True
    )
    stage = models.CharField(
        max_length=20, choices=Stage.choices, blank=True
    )  # current, or waiting at
    input_kind = models.CharField(max_length=20, choices=InputKind.choices, blank=True)
    # Per-problem settings snapshot (provider, model, review, etc.)
    settings = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    if TYPE_CHECKING:
        stages: "RelatedManager[ItemStage]"

    class Meta:
        ordering = ["position", "-created_at"]

    def __str__(self) -> str:
        return f"{self.problem.slug} ({self.status})"


# Backward compatibility alias
TaskItem = PipelineItem


class ItemStage(models.Model):
    """The state of one stage of one item: the rows behind the item's timeline."""

    class Status(models.TextChoices):
        PENDING = "pending"
        RUNNING = "running"
        DONE = "done"
        SKIPPED = "skipped"  # not needed (already had the data, or review is off)
        WAITING = "waiting"  # waiting for you
        FAILED = "failed"

    item = models.ForeignKey(
        PipelineItem, on_delete=models.CASCADE, related_name="stages"
    )
    stage = models.CharField(max_length=20, choices=Stage.choices)
    order = models.PositiveSmallIntegerField(default=0)  # position in the pipeline
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING
    )
    detail = models.CharField(max_length=300, blank=True)  # e.g. "already fetched"
    error = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(
                fields=["item", "stage"], name="one_row_per_stage_per_item"
            )
        ]

    def __str__(self) -> str:
        return f"{self.item.problem.slug}: {self.stage} ({self.status})"
