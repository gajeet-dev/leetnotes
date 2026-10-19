from django.db import models

from problems.models import Problem


class AIResponse(models.Model):
    class Source(models.TextChoices):
        PROVIDER = "provider"
        MANUAL = "manual"

    class Status(models.TextChoices):
        VALID = "valid"
        INVALID = "invalid"

    problem = models.ForeignKey(
        Problem, on_delete=models.CASCADE, related_name="ai_responses"
    )
    source = models.CharField(max_length=10, choices=Source.choices)
    provider_name = models.CharField(
        max_length=50, blank=True
    )  # "claude", "agy", "claude.ai"
    model_name = models.CharField(max_length=100, blank=True)
    prompt_version = models.CharField(max_length=16)
    input_hash = models.CharField(max_length=64)  # lets us detect stale responses later
    raw_text = models.TextField()
    parsed = models.JSONField(null=True, blank=True)  # validated SolutionNote as a dict
    status = models.CharField(max_length=10, choices=Status.choices, db_index=True)
    error = models.TextField(blank=True)
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["problem"],
                condition=models.Q(is_active=True),
                name="one_active_response_per_problem",
            )
        ]

    def __str__(self) -> str:
        return f"{self.problem.slug} ({self.status}, {self.created_at:%Y-%m-%d %H:%M})"
