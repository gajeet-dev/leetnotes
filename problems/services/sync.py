from collections.abc import Callable
from dataclasses import dataclass

import structlog
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from structlog.contextvars import bound_contextvars

from leetcode.client import LeetCodeClient
from leetcode.exceptions import (
    AuthError,
    LeetCodeError,
    RateLimitError,
)
from leetcode.images import ImageDownloader

from ..models import Problem, Submission, SyncTask
from .content import build_problem_content
from .images import process_problem_images

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class DiscoveryResult:
    solved_total: int
    new_problems: int
    new_tasks: int


def discover(client: LeetCodeClient) -> DiscoveryResult:
    """Fetch the solved list once and create any missing problems and tasks.

    Idempotent: existing problems and tasks (including their status) are
    left untouched, so this never resets progress.
    """
    solved = client.list_all_problems()
    if not solved:
        logger.warning("discovery_found_no_solved_problems")
        return DiscoveryResult(0, 0, 0)

    slugs = [item.slug for item in solved]
    base_url = settings.LEETCODE_BASE_URL

    with transaction.atomic():
        problems_before = Problem.objects.count()
        tasks_before = SyncTask.objects.count()

        Problem.objects.bulk_create(
            [
                Problem(
                    leetcode_id=item.leetcode_id,
                    slug=item.slug,
                    title=item.title,
                    url=f"{base_url}/problems/{item.slug}/",
                )
                for item in solved
            ],
            ignore_conflicts=True,  # slug is unique: existing rows are skipped
        )

        SyncTask.objects.bulk_create(
            [
                SyncTask(problem=problem, part=part)
                for problem in Problem.objects.filter(slug__in=slugs)
                for part in SyncTask.Part.values
            ],
            ignore_conflicts=True,  # (problem, part) is unique
        )

        result = DiscoveryResult(
            solved_total=len(solved),
            new_problems=Problem.objects.count() - problems_before,
            new_tasks=SyncTask.objects.count() - tasks_before,
        )

    logger.info(
        "discovery_completed",
        solved_total=result.solved_total,
        new_problems=result.new_problems,
        new_tasks=result.new_tasks,
    )
    return result


@dataclass(frozen=True)
class Deps:
    client: LeetCodeClient
    downloader: ImageDownloader


@dataclass
class ProcessResult:
    done: int = 0
    failed: int = 0
    skipped: int = 0
    stopped_reason: str | None = None


# --- task bookkeeping ---


def _complete(task: SyncTask) -> None:
    task.status = SyncTask.Status.DONE
    task.completed_at = timezone.now()
    task.last_error = ""
    task.save(update_fields=["status", "completed_at", "last_error"])


def _fail(task: SyncTask, exc: Exception) -> None:
    task.status = SyncTask.Status.FAILED
    task.last_error = f"{type(exc).__name__}: {exc}"
    task.save(update_fields=["status", "last_error"])


# --- handlers: one per part ---
# Each fetches first (no transaction held during network I/O), then writes the
# data and marks the task done inside one atomic block. All are idempotent, so
# a crash at any point just means the task is still pending and safe to rerun.


def _sync_content(task: SyncTask, deps: Deps) -> None:
    problem = task.problem
    dto = deps.client.get_problem(problem.slug)

    with transaction.atomic():
        problem.leetcode_id = dto.leetcode_id
        problem.title = dto.title
        problem.difficulty = dto.difficulty
        problem.category = dto.category
        problem.tags = [tag.model_dump() for tag in dto.tags]
        problem.solved = dto.solved
        problem.raw_question_html = dto.raw_question_html
        # Fresh HTML invalidates any earlier image rewrite.
        problem.content_local_html = None
        problem.has_images = None
        problem.save(
            update_fields=[
                "leetcode_id",
                "title",
                "difficulty",
                "category",
                "tags",
                "raw_question_html",
                "content_local_html",
                "has_images",
                "updated_at",
            ]
        )
        build_problem_content(problem)
        _complete(task)


def _sync_images(task: SyncTask, deps: Deps) -> None:
    problem = task.problem
    process_problem_images(problem, deps.downloader)

    with transaction.atomic():
        build_problem_content(problem)  # markdown now uses the rewritten image paths
        _complete(task)


def _latest_accepted(client: LeetCodeClient, slug: str):
    """Return the newest accepted submission summary, or None."""
    # LeetCode lists submissions newest first, so the first accepted one wins
    # and we never fetch more pages than needed.
    for summary in client.iter_submissions(slug):
        if summary.status == "Accepted":
            return summary
    return None


def _sync_submissions(task: SyncTask, deps: Deps) -> None:
    problem = task.problem

    # A pinned solution is never replaced, even on an explicit refetch.
    # Checked before any network call. Unpin it first to allow a replacement.
    if problem.submissions.filter(pinned=True).exists():
        logger.info("submission_sync_skipped", reason="pinned")
        with transaction.atomic():
            _complete(task)
        return

    latest = _latest_accepted(deps.client, problem.slug)

    if latest is None:
        logger.warning("no_accepted_submission_found")
        with transaction.atomic():
            _complete(task)
        return

    already_stored = problem.submissions.filter(
        submission_date=latest.timestamp
    ).exists()
    detail = None if already_stored else deps.client.get_submission_details(latest.id)

    with transaction.atomic():
        if detail is not None:
            Submission.objects.create(
                problem=problem,
                lang=detail.lang,
                code=detail.code,
                submission_date=detail.submission_date,
            )
            # Keep only the latest. Nothing is pinned at this point, so any
            # other row for this problem is an older solution.
            problem.submissions.exclude(submission_date=detail.submission_date).delete()
        _complete(task)


HANDLERS: dict[str, Callable[[SyncTask, Deps], None]] = {
    SyncTask.Part.CONTENT: _sync_content,
    SyncTask.Part.IMAGES: _sync_images,
    SyncTask.Part.SUBMISSIONS: _sync_submissions,
}

# Images need the content's HTML, so content always goes first.
PART_ORDER = (
    SyncTask.Part.CONTENT,
    SyncTask.Part.IMAGES,
    SyncTask.Part.SUBMISSIONS,
)


# --- the runner ---


def process_pending(
    client: LeetCodeClient,
    downloader: ImageDownloader,
    *,
    slug: str | None = None,
    limit: int | None = None,
    retry_failed: bool = False,
) -> ProcessResult:
    """Run every pending task, one problem at a time.

    Stops the whole run on AuthError/RateLimitError (every later request
    would fail too) and leaves the remaining tasks pending. Any other
    LeetCodeError marks just that task FAILED and moves on.
    """
    deps = Deps(client, downloader)
    result = ProcessResult()

    statuses = [SyncTask.Status.PENDING]
    if retry_failed:
        statuses.append(SyncTask.Status.FAILED)

    queryset = SyncTask.objects.filter(status__in=statuses)
    if slug:
        queryset = queryset.filter(problem__slug=slug)

    problem_ids = list(
        queryset.order_by("problem_id").values_list("problem_id", flat=True).distinct()
    )
    if limit:
        problem_ids = problem_ids[:limit]

    for problem_id in problem_ids:
        problem = Problem.objects.get(pk=problem_id)
        tasks = sorted(
            queryset.filter(problem_id=problem_id),
            key=lambda t: PART_ORDER.index(t.part),
        )
        for task in tasks:
            task.problem = (
                problem  # one shared instance: later tasks see earlier writes
            )
            with bound_contextvars(slug=problem.slug, part=task.part):
                if task.part == SyncTask.Part.IMAGES and not _content_done(task):
                    logger.warning("task_skipped", reason="content_not_done")
                    result.skipped += 1
                    continue

                task.attempts += 1
                task.save(update_fields=["attempts"])

                try:
                    HANDLERS[task.part](task, deps)
                except (AuthError, RateLimitError) as exc:
                    logger.error("sync_aborted", error=str(exc))
                    result.stopped_reason = type(exc).__name__
                    return result
                except LeetCodeError as exc:
                    logger.warning("task_failed", error=str(exc))
                    _fail(task, exc)
                    result.failed += 1
                else:
                    logger.info("task_done")
                    result.done += 1

    logger.info(
        "sync_finished",
        done=result.done,
        failed=result.failed,
        skipped=result.skipped,
    )
    return result


def _content_done(task: SyncTask) -> bool:
    return task.problem.sync_tasks.filter(
        part=SyncTask.Part.CONTENT, status=SyncTask.Status.DONE
    ).exists()
