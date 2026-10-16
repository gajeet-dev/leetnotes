import structlog
from django.core.files.base import ContentFile

from leetcode.exceptions import TransportError
from leetcode.images import ImageDownloader
from leetcode.mappers.question_content.images import rewrite_images

from ..models import Problem, ProblemImage

logger = structlog.get_logger(__name__)


def process_problem_images(problem: Problem, downloader: ImageDownloader) -> Problem:
    """Download a problem's images and store the rewritten HTML on the problem."""
    log = logger.bind(slug=problem.slug)

    def resolve(idx: int, src: str) -> str | None:
        image, _ = ProblemImage.objects.get_or_create(problem=problem, source_url=src)
        if not image.file:  # already downloaded -> skip, like the old cache check
            try:
                downloaded = downloader.download(src)
            except TransportError as exc:
                log.warning("image_download_failed", url=src, error=str(exc))
                return None
            image.file.save(
                f"{idx}.{downloaded.extension}",
                ContentFile(downloaded.content),
                save=True,
            )
        return image.file.name  # e.g. problems/two-sum/0.png

    local_html, total = rewrite_images(problem.raw_question_html or "", resolve)

    problem.has_images = total > 0
    if total:
        problem.content_local_html = local_html
    else:
        # Nothing to localize: local is the same as remote. Copy the original
        # string rather than the re-serialized one, so it's byte-identical.
        problem.content_local_html = problem.raw_question_html
    problem.save(update_fields=["has_images", "content_local_html", "updated_at"])

    log.info("image_processing_completed", total=total)
    return problem
