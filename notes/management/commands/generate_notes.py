from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from notes.generation import (
    describe_result,
    describe_totals,
    error_details,
    generate_notes,
    has_errors,
    tally,
)
from problems.models import Problem


class Command(BaseCommand):
    help = "Write problem notes and solution notes to the output folder(s)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--slug", action="append", help="Only this problem (repeatable)"
        )
        parser.add_argument("--limit", type=int, help="At most this many problems")
        parser.add_argument(
            "--output",
            action="append",
            help="Output folder instead of NOTES_OUTPUT_DIRS, for this run only (repeatable)",
        )
        parser.add_argument(
            "--overwrite-solutions",
            action="store_true",
            help="Replace existing solution notes (the old ones are backed up). Needs --slug.",
        )

    def handle(self, *args, **opts):
        self.check_options(opts)
        problems = self.select_problems(opts["slug"], opts["limit"])
        if not problems:
            self.stdout.write("No problems to generate notes for.")
            return
        results = self.generate_all(problems, opts)
        self.report(results)

    # --- before the run ---

    def check_options(self, opts):
        if opts["overwrite_solutions"] and not opts["slug"]:
            raise CommandError(
                "--overwrite-solutions needs --slug, so you choose exactly "
                "which solution notes to replace."
            )

    def select_problems(self, slugs, limit) -> list[Problem]:
        if slugs:
            problems = self.problems_by_slug(slugs)
        else:
            problems = list(Problem.objects.filter(solved=True).order_by("leetcode_id"))
        return problems[:limit] if limit else problems

    def problems_by_slug(self, slugs) -> list[Problem]:
        found = {p.slug: p for p in Problem.objects.filter(slug__in=slugs)}
        missing = [slug for slug in slugs if slug not in found]
        if missing:
            raise CommandError(f"No such problem: {', '.join(missing)}")
        return [found[slug] for slug in slugs]  # keep the order given

    # --- the run ---

    def generate_all(self, problems, opts) -> list:
        output_dirs = self.output_dirs(opts["output"])
        results = []
        for index, problem in enumerate(problems, start=1):
            result = generate_notes(
                problem, output_dirs, overwrite_solution=opts["overwrite_solutions"]
            )
            self.print_result(index, len(problems), result)
            results.append(result)
        return results

    def output_dirs(self, given) -> list[Path] | None:
        return [Path(path).expanduser() for path in given] if given else None

    # --- reporting ---

    def print_result(self, index, total, result):
        line = f"[{index}/{total}] {describe_result(result)}"
        if has_errors(result):
            self.stdout.write(self.style.ERROR(line))
            for detail in error_details(result):
                self.stdout.write(self.style.ERROR(detail))
        else:
            self.stdout.write(line)

    def report(self, results):
        totals = tally(results)
        self.stdout.write(describe_totals(totals))
        if totals.failed:
            raise CommandError(
                f"{totals.failed} problem(s) had write errors (see the lines above)."
            )
