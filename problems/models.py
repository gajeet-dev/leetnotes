from django.db import models


class Problem(models.Model):
    class Difficulty(models.TextChoices):
        EASY = "Easy"
        MEDIUM = "Medium"
        HARD = "Hard"

    class Category(models.TextChoices):
        ALGORITHM = "Algorithm"
        DATABASE = "Database"

    slug = models.SlugField(max_length=200, unique=True)
    leetcode_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    title = models.CharField(max_length=300, null=True, blank=True)
    url = models.URLField(max_length=500, null=True, blank=True)
    difficulty = models.CharField(
        max_length=10, choices=Difficulty.choices, null=True, blank=True
    )
    category = models.CharField(
        max_length=100, choices=Category.choices, null=True, blank=True
    )
    tags = models.JSONField(null=True, blank=True)  # list[dict]
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
        return f"{self.leetcode_id}. {self.title or self.slug}"

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
    return f"problems/{instance.problem.slug}/{filename}"


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
