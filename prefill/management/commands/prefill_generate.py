import time

from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Exists, OuterRef

from prefill.models import AIResponse
from prefill.prompt_builder import PromptInputError
from prefill.providers.base import ProviderError, ProviderUnavailable
from prefill.providers.registry import get_provider
from prefill.services import generate_for_problem
from problems.models import Problem


class Command(BaseCommand):
    help = "Generate AI solution notes. Default: solved problems that have no active note yet."

    def add_arguments(self, parser):
        parser.add_argument(
            "--provider",
            help="Name from PREFILL_PROVIDERS (default: the configured default)",
        )
        parser.add_argument("--slug", help="One problem, even if it already has a note")
        parser.add_argument("--limit", type=int, help="At most this many problems")
        parser.add_argument(
            "--delay", type=float, default=10.0, help="Seconds between calls"
        )

    def _select(self, slug, limit):
        if slug:
            problems = Problem.objects.filter(slug=slug)
            if not problems.exists():
                raise CommandError(f"No problem with slug {slug!r}")
        else:
            problems = (
                Problem.objects.filter(solved=True, submissions__isnull=False)
                .exclude(content_remote_markdown__isnull=True)
                .annotate(
                    has_note=Exists(
                        AIResponse.objects.filter(
                            problem=OuterRef("pk"), is_active=True
                        )
                    )
                )
                .filter(has_note=False)
                .distinct()
                .order_by("leetcode_id")
            )
        return list(problems[:limit] if limit else problems)

    def handle(self, *args, **opts):
        provider = self.load_provider(opts["provider"])
        problems = self._select(opts["slug"], opts["limit"])
        if not problems:
            self.stdout.write("Nothing to generate.")
            return

        self.stdout.write(f"Generating {len(problems)} note(s) with {provider.name}.")
        counts = {"ok": 0, "invalid": 0, "failed": 0, "skipped": 0}

        try:
            for index, problem in enumerate(problems, start=1):
                label = f"[{index}/{len(problems)}] {problem.slug}"
                try:
                    response = generate_for_problem(problem, provider)
                except PromptInputError as exc:
                    counts["skipped"] += 1
                    self.stdout.write(f"{label}: skipped ({exc})")
                except ProviderUnavailable as exc:
                    self.stdout.write(self.style.ERROR(f"{label}: {exc}"))
                    self.stdout.write("Stopping: later calls would fail too.")
                    break
                except ProviderError as exc:
                    counts["failed"] += 1
                    self.stdout.write(self.style.WARNING(f"{label}: failed ({exc})"))
                else:
                    if response.status == AIResponse.Status.VALID:
                        counts["ok"] += 1
                        self.stdout.write(f"{label}: ok")
                    else:
                        counts["invalid"] += 1
                        self.stdout.write(
                            self.style.WARNING(
                                f"{label}: invalid output saved (see AI responses)"
                            )
                        )

                if index < len(problems):
                    time.sleep(opts["delay"])
        except KeyboardInterrupt:
            self.stdout.write(
                "\nInterrupted. Finished notes are saved; run again to continue."
            )

        self.stdout.write(
            f"Done: {counts['ok']} ok, {counts['invalid']} invalid, "
            f"{counts['failed']} failed, {counts['skipped']} skipped."
        )

    def load_provider(self, name):
        try:
            return get_provider(name)
        except ImproperlyConfigured as exc:
            raise CommandError(str(exc)) from exc
