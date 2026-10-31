"""Run the pipeline worker in this terminal."""

import signal

from django.core.management.base import BaseCommand, CommandError

from pipeline.summary import describe_recent_tasks
from pipeline.worker import Worker


def make_worker(idle_timeout: float | None) -> Worker:
    return Worker(idle_timeout=idle_timeout)


class Command(BaseCommand):
    help = (
        "Run queued tasks until nothing is left to do. Ctrl+C stops after the current "
        "stage, and anything interrupted resumes the next time."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--forever",
            action="store_true",
            help="Keep waiting for new tasks instead of exiting when idle",
        )
        parser.add_argument(
            "--idle-timeout",
            type=float,
            default=0.0,
            help="Seconds to wait with nothing to do before exiting (ignored with --forever)",
        )

    def handle(self, *args, **opts):
        worker = make_worker(None if opts["forever"] else opts["idle_timeout"])
        self.install_signal_handlers(worker)
        self.stdout.write(
            "Worker started. Ctrl+C stops it after the current stage; "
            "press it twice to quit right away."
        )
        if not self.run_until_done(worker):
            raise CommandError(
                "Another worker is already running, so this one did nothing."
            )
        self.print_summary()

    def install_signal_handlers(self, worker: Worker) -> None:
        presses = 0

        def on_signal(signum, frame):
            nonlocal presses
            presses += 1
            if presses == 1:
                self.stdout.write(
                    "\nStopping after the current stage (press Ctrl+C again to quit now)..."
                )
                worker.stop()
            else:
                raise KeyboardInterrupt

        signal.signal(signal.SIGINT, on_signal)
        signal.signal(signal.SIGTERM, on_signal)

    def run_until_done(self, worker: Worker) -> bool:
        """Returns False if another worker already holds the lock."""
        try:
            return worker.run()
        except KeyboardInterrupt:
            self.stdout.write(
                "\nQuit now. Anything that was interrupted resumes next time."
            )
            return True

    def print_summary(self) -> None:
        lines = describe_recent_tasks()
        self.stdout.write("Tasks:" if lines else "No tasks.")
        for line in lines:
            self.stdout.write(f"  {line}")
