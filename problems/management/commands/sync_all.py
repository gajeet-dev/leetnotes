import fcntl
import random
import time

import structlog
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from leetcode.bootstrap import get_client, get_image_downloader
from problems.models import SyncTask
from problems.services.sync import PACE_FROM_SETTINGS, process_pending


logger = structlog.get_logger(__name__)


def _pending_problem_count() -> int:
    return (
        SyncTask.objects.filter(status=SyncTask.Status.PENDING)
        .values("problem_id")
        .distinct()
        .count()
    )


class Command(BaseCommand):
    help = (
        "Sync every pending problem in batches with a pause between batches. "
        "Safe to stop at any time: run it again to resume."
    )

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=100)
        parser.add_argument("--pause-min", type=float, default=7, help="minutes")
        parser.add_argument("--pause-max", type=float, default=12, help="minutes")
        parser.add_argument(
            "--problems-per-minute",
            type=float,
            default=None,
            help="Override the saved pace for this run",
        )
        parser.add_argument(
            "--retry-failed",
            action="store_true",
            help="Reset failed tasks to pending before starting",
        )

    def handle(self, *args, **opts):
        # Only one runner at a time. The OS drops the lock if the process
        # dies, so a crash or power loss never leaves a stale lock behind.
        lock_file = open(settings.BASE_DIR / ".sync.lock", "w")
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CommandError("Another sync_all is already running.")

        if opts["retry_failed"]:
            reset = SyncTask.objects.filter(status=SyncTask.Status.FAILED).update(
                status=SyncTask.Status.PENDING,
                attempts=0,
                last_error="",
                completed_at=None,
            )
            self.stdout.write(f"Reset {reset} failed task(s) to pending.")

        client = get_client()
        downloader = get_image_downloader()
        batch_no = 0

        try:
            while _pending_problem_count():
                batch_no += 1
                self.stdout.write(
                    f"Batch {batch_no}: {_pending_problem_count()} problem(s) "
                    "still pending."
                )

                result = process_pending(
                    client,
                    downloader,
                    limit=opts["batch_size"],
                    problems_per_minute=opts["problems_per_minute"]
                    or PACE_FROM_SETTINGS,
                )
                self.stdout.write(
                    f"  done={result.done} failed={result.failed} "
                    f"skipped={result.skipped}"
                )

                if result.stopped_reason:
                    raise CommandError(
                        f"Stopped: {result.stopped_reason}. Fix the cause "
                        "(usually an expired cookie in .env) and run again."
                    )
                if result.done + result.failed == 0:
                    self.stdout.write(
                        "No progress this batch: the remaining tasks are "
                        "blocked (for example images waiting on failed "
                        "content). Use --retry-failed to try them again."
                    )
                    break
                if not _pending_problem_count():
                    break

                pause = random.uniform(opts["pause_min"], opts["pause_max"]) * 60
                self.stdout.write(
                    f"  pausing {pause / 60:.1f} min before next batch..."
                )
                time.sleep(pause)
        except KeyboardInterrupt:
            self.stdout.write("\nInterrupted. Progress is saved; run again to resume.")
            return

        self.stdout.write(self.style.SUCCESS("Nothing left to sync."))
