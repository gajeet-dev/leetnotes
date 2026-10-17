import random
import time
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

from collections.abc import Iterable
from datetime import datetime

from django.db.models import Max


@dataclass
class RecentCheckResult:
    checked: int = 0  # entries in LeetCode's recent list
    reset: int = 0  # submissions tasks set back to pending
    up_to_date: int = 0  # stored solution is already the newest
    pinned_skipped: int = 0  # protected by a pin
    unknown: int = 0  # slug not in our DB: run Discover
    newly_solved: int = 0  # known problems we had flagged unsolved


def refresh_recent_submissions(
    client: LeetCodeClient, *, limit: int = 20
) -> RecentCheckResult:
    """Reset the submissions task for problems solved again since our last fetch."""
    recent = client.get_recent_accepted(limit=limit)
    result = RecentCheckResult(checked=len(recent))

    # A problem can appear more than once in the list; keep its newest timestamp.
    newest: dict[str, datetime] = {}
    for item in recent:
        if item.slug not in newest or item.timestamp > newest[item.slug]:
            newest[item.slug] = item.timestamp

    problems = {p.slug: p for p in Problem.objects.filter(slug__in=newest)}

    with transaction.atomic():
        for slug, timestamp in newest.items():
            problem = problems.get(slug)
            if problem is None:
                result.unknown += 1
                continue

            if problem.submissions.filter(pinned=True).exists():
                result.pinned_skipped += 1
                continue

            latest_stored = problem.submissions.aggregate(
                latest=Max("submission_date")
            )["latest"]
            if latest_stored is not None and timestamp <= latest_stored:
                result.up_to_date += 1
                continue

            if not problem.solved:
                problem.solved = True
                problem.save(update_fields=["solved", "updated_at"])
                result.newly_solved += 1

            task, created = SyncTask.objects.get_or_create(
                problem=problem, part=SyncTask.Part.SUBMISSIONS
            )
            if not created:
                task.status = SyncTask.Status.PENDING
                task.attempts = 0
                task.last_error = ""
                task.completed_at = None
                task.save()
            result.reset += 1

    logger.info("recent_check_completed", **result.__dict__)
    return result


# What must be redone when a part is refetched.
REFETCH_CASCADE: dict[SyncTask.Part, tuple[SyncTask.Part, ...]] = {
    # New HTML invalidates the image rewrite and everything derived from it.
    SyncTask.Part.CONTENT: (SyncTask.Part.CONTENT, SyncTask.Part.IMAGES),
    SyncTask.Part.IMAGES: (SyncTask.Part.IMAGES,),
    SyncTask.Part.SUBMISSIONS: (SyncTask.Part.SUBMISSIONS,),
}


@dataclass
class RefetchResult:
    tasks_reset: int = 0
    pinned_skipped: int = 0  # problems whose solution is pinned
    missing: int = 0  # problems with no task for that part


def mark_for_refetch(problem_ids: Iterable[int], part: SyncTask.Part) -> RefetchResult:
    """Reset a part (and whatever depends on it) back to pending.

    Only flips task status. The next sync run does the actual fetching.
    Pinned solutions are never reset, and unsolved problems have no
    submissions task, so those are reported rather than silently ignored.
    """
    ids = list(problem_ids)
    result = RefetchResult()

    if part == SyncTask.Part.SUBMISSIONS:
        pinned = set(
            Submission.objects.filter(problem_id__in=ids, pinned=True).values_list(
                "problem_id", flat=True
            )
        )
        result.pinned_skipped = len(pinned)
        ids = [i for i in ids if i not in pinned]

    with transaction.atomic():
        has_task = set(
            SyncTask.objects.filter(problem_id__in=ids, part=part).values_list(
                "problem_id", flat=True
            )
        )
        result.missing = len(ids) - len(has_task)

        result.tasks_reset = (
            SyncTask.objects.filter(
                problem_id__in=has_task, part__in=REFETCH_CASCADE[part]
            )
            .exclude(status=SyncTask.Status.PENDING)
            .update(
                status=SyncTask.Status.PENDING,
                attempts=0,
                last_error="",
                completed_at=None,
            )
        )

    logger.info(
        "refetch_marked",
        part=part,
        tasks_reset=result.tasks_reset,
        pinned_skipped=result.pinned_skipped,
        missing=result.missing,
    )
    return result


def _pace(started_at: float, problems_per_minute: float | None) -> None:
    """Sleep so one problem takes at least 60/N seconds, plus up to 20% jitter."""
    if not problems_per_minute:
        return
    target = 60.0 / problems_per_minute
    remaining = target - (time.monotonic() - started_at)
    if remaining > 0:
        wait = remaining + random.uniform(0, target * 0.2)
        logger.info("pacing_sleep", seconds=round(wait, 1))
        time.sleep(wait)


@dataclass(frozen=True)
class DiscoveryResult:
    total: int
    new_problems: int
    newly_solved: int
    new_tasks: int


def discover(client: LeetCodeClient) -> DiscoveryResult:
    """Sync the full problem catalog and the user's solved status.

    Idempotent. New problems get content and image tasks. A submissions task
    exists only for solved problems, so a problem that becomes solved gets
    one on the next discovery. Existing task progress is never reset.
    """
    problems = client.list_all_problems()
    if not problems:
        logger.warning("discovery_found_no_problems")
        return DiscoveryResult(0, 0, 0, 0)

    base_url = settings.LEETCODE_BASE_URL
    solved_slugs = [p.slug for p in problems if p.solved]

    with transaction.atomic():
        problems_before = Problem.objects.count()
        tasks_before = SyncTask.objects.count()

        Problem.objects.bulk_create(
            [
                Problem(
                    slug=p.slug,
                    title=p.title,
                    url=f"{base_url}/problems/{p.slug}/",
                    leetcode_id=p.leetcode_id,
                    solved=p.solved,
                    paid_only=p.paid_only,
                    difficulty=p.difficulty,
                )
                for p in problems
            ],
            ignore_conflicts=True,  # existing slugs are skipped
        )

        # Promote problems that were unsolved in our DB. Brand-new rows were
        # already inserted with the right flag, so this only touches old ones.
        newly_solved = Problem.objects.filter(
            slug__in=solved_slugs, solved=False
        ).update(solved=True)

        tasks = []
        for problem in Problem.objects.all():
            parts = []
            if not problem.paid_only:
                parts += [SyncTask.Part.CONTENT, SyncTask.Part.IMAGES]

            if problem.solved:
                parts.append(SyncTask.Part.SUBMISSIONS)

            tasks += [SyncTask(problem=problem, part=part) for part in parts]

        SyncTask.objects.bulk_create(tasks, ignore_conflicts=True)

        result = DiscoveryResult(
            total=len(problems),
            new_problems=Problem.objects.count() - problems_before,
            newly_solved=newly_solved,
            new_tasks=SyncTask.objects.count() - tasks_before,
        )

    logger.info("discovery_completed", **result.__dict__)
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
    client,
    downloader,
    *,
    slug=None,
    problem_ids=None,
    limit=None,
    retry_failed=False,
    problems_per_minute: float | None = settings.LEETCODE_PROBLEMS_PER_MINUTE,
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
    if problem_ids is not None:
        queryset = queryset.filter(problem_id__in=problem_ids)

    problem_ids_to_run = list(
        queryset.order_by("-problem__solved", "problem_id")
        .values_list("problem_id", flat=True)
        .distinct()
    )
    if limit:
        problem_ids_to_run = problem_ids_to_run[:limit]

    for index, problem_id in enumerate(problem_ids_to_run):
        started = time.monotonic()
        did_work = False
        problem = Problem.objects.get(pk=problem_id)
        tasks = sorted(
            queryset.filter(problem_id=problem_id),
            key=lambda t: PART_ORDER.index(t.part),
        )
        for task in tasks:
            task.problem = problem
            with bound_contextvars(slug=problem.slug, part=task.part):
                if task.part == SyncTask.Part.IMAGES and not _content_done(task):
                    logger.warning("task_skipped", reason="content_not_done")
                    result.skipped += 1
                    continue

                task.attempts += 1
                task.save(update_fields=["attempts"])
                did_work = True

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

                is_last = index == len(problem_ids_to_run) - 1
                if did_work and not is_last:
                    _pace(started, problems_per_minute)

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
