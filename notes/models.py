from pathlib import Path

from django.db import models

from problems.models import Problem


class NoteRecord(models.Model):
    """What we last wrote for one note in one output folder.

    The content hash is what lets us tell, later, whether you edited the
    file: the note on disk either still matches the hash or it doesn't.
    """

    class Kind(models.TextChoices):
        PROBLEM = "problem"
        SOLUTION = "solution"

    problem = models.ForeignKey(
        Problem, on_delete=models.CASCADE, related_name="note_records"
    )
    kind = models.CharField(max_length=10, choices=Kind.choices)
    output_dir = models.CharField(max_length=500)  # the output folder, as configured
    relative_path = models.CharField(
        max_length=500
    )  # e.g. Leetcode/problems/0001-two-sum.md
    content_hash = models.CharField(max_length=64)
    written_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["problem", "kind", "output_dir"],
                name="one_record_per_note_per_folder",
            )
        ]

    def __str__(self) -> str:
        return f"{self.problem.slug} {self.kind} -> {self.relative_path}"

    @property
    def full_path(self) -> Path:
        return Path(self.output_dir) / self.relative_path
