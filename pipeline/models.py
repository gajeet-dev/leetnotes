from typing import TYPE_CHECKING

from django.db import models

from problems.models import Problem

from .stages import Stage

if TYPE_CHECKING:
    from django.db.models.manager import RelatedManager


class Task(models.Model):
    """One run: a set of problems that go through the pipeline together."""

    class Status(models.TextChoices):
        ACTIVE = "active"  # the worker may pick up its items
        PAUSED = "paused"  # by you, or by an error that needs fixing (see pause_reason)
        CANCELLED = "cancelled"
        FINISHED = "finished"  # every item has reached the end

    name = models.CharField(max_length=200, blank=True)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )
    pause_reason = models.TextField(blank=True)
    # A snapshot of the settings at creation, so changing your defaults later
    # can't alter a task that is already running.
    settings = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    if TYPE_CHECKING:
        items: "RelatedManager[TaskItem]"

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.name or f"Task {self.pk}"


class TaskItem(models.Model):
    """One problem inside a task."""

    class Status(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        NEEDS_INPUT = "needs_input"  # waiting for you: see input_kind
        COMPLETED = "completed"
        FAILED = "failed"

    class InputKind(models.TextChoices):
        MANUAL_PREFILL = "manual_prefill"  # paste a reply from a web chat
        REVIEW = "review"  # approve the AI note before it is saved

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="items")
    problem = models.ForeignKey(
        Problem, on_delete=models.CASCADE, related_name="task_items"
    )
    position = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.QUEUED, db_index=True
    )
    stage = models.CharField(
        max_length=20, choices=Stage.choices, blank=True
    )  # current, or waiting at
    input_kind = models.CharField(max_length=20, choices=InputKind.choices, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    if TYPE_CHECKING:
        stages: "RelatedManager[ItemStage]"

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(
                fields=["task", "problem"], name="one_item_per_problem_per_task"
            )
        ]

    def __str__(self) -> str:
        return f"{self.problem.slug} ({self.status})"


class ItemStage(models.Model):
    """The state of one stage of one item: the rows behind the item's timeline."""

    class Status(models.TextChoices):
        PENDING = "pending"
        RUNNING = "running"
        DONE = "done"
        SKIPPED = "skipped"  # not needed (already had the data, or review is off)
        WAITING = "waiting"  # waiting for you
        FAILED = "failed"

    item = models.ForeignKey(TaskItem, on_delete=models.CASCADE, related_name="stages")
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
