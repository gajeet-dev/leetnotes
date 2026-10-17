from django.db import models


class Problem(models.Model):
    class Difficulty(models.TextChoices):
        EASY = "Easy"
        MEDIUM = "Medium"
        HARD = "Hard"

    class Category(models.TextChoices):
        ALGORITHMS = "Algorithms"
        DATABASE = "Database"
        SHELL = "Shell"
        CONCURRENCY = "Concurrency"
        JAVASCRIPT = "JavaScript"
        PANDAS = "pandas"

    slug = models.SlugField(max_length=200, unique=True)
    leetcode_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    title = models.CharField(max_length=300, null=True, blank=True, db_index=True)
    url = models.URLField(max_length=500, null=True, blank=True)
    difficulty = models.CharField(
        max_length=10, choices=Difficulty.choices, null=True, blank=True
    )
    category = models.CharField(
        max_length=20, choices=Category.choices, null=True, blank=True, db_index=True
    )
    tags = models.JSONField(null=True, blank=True)  # list[dict]
    solved = models.BooleanField(default=False, db_index=True)
    paid_only = models.BooleanField(default=False)
    raw_question_html = models.TextField(null=True, blank=True)

    # Question content (was the nested QuestionContent model)
    content_remote_markdown = models.TextField(null=True, blank=True)
    content_local_html = models.TextField(null=True, blank=True)
    content_local_markdown = models.TextField(null=True, blank=True)
    content_text = models.TextField(null=True, blank=True)

    # None = images part not processed yet.
    # False = processed, question has no <img> tags.
    # True = question has images (imgs_local_paths may still be empty
    #        if every download failed).
    has_images = models.BooleanField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["leetcode_id"]

    def __str__(self) -> str:
        name = self.title or self.slug
        return f"{self.leetcode_id}. {name}" if self.leetcode_id is not None else name

    @property
    def images_populated(self) -> bool:
        """
        True when the question has no images (has_images is False), or it
        does and at least one was downloaded. False when images exist but
        every download failed, or the images part hasn't run yet.
        """
        return self.has_images is False or self.images.exclude(file="").exists()  # pyright: ignore[reportAttributeAccessIssue]


class Submission(models.Model):
    problem = models.ForeignKey(
        Problem, on_delete=models.CASCADE, related_name="submissions"
    )
    lang = models.CharField(max_length=50)
    code = models.TextField()
    submission_date = models.DateTimeField()
    pinned = models.BooleanField(default=False)

    class Meta:
        ordering = ["-submission_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["problem", "submission_date"],
                name="unique_submission_per_problem_date",
            )
        ]

    def __str__(self) -> str:
        return f"{self.problem.slug} ({self.lang}, {self.submission_date:%Y-%m-%d})"


def problem_image_path(instance, filename):
    return f"assets/{instance.problem.slug}/{filename}"


class ProblemImage(models.Model):
    problem = models.ForeignKey(
        Problem, on_delete=models.CASCADE, related_name="images"
    )
    source_url = models.URLField(max_length=1000)
    file = models.ImageField(upload_to=problem_image_path)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["problem", "source_url"], name="unique_image_per_problem"
            )
        ]


class SyncTask(models.Model):
    """One unit of fetch work: a single part of a single problem."""

    class Part(models.TextChoices):
        CONTENT = "content"
        SUBMISSIONS = "submissions"
        IMAGES = "images"

    class Status(models.TextChoices):
        PENDING = "pending"
        DONE = "done"
        FAILED = "failed"

    problem = models.ForeignKey(
        Problem, on_delete=models.CASCADE, related_name="sync_tasks"
    )
    part = models.CharField(max_length=20, choices=Part.choices)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["problem", "part"], name="unique_task_per_part"
            )
        ]

    def __str__(self) -> str:
        return f"{self.problem.slug}:{self.part} ({self.status})"
