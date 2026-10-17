import shutil

import structlog
from django.conf import settings
from django.core.management.base import BaseCommand

from leetcode.mappers.question_content.images import rewrite_images
from problems.models import Problem, ProblemImage
from problems.services.content import build_problem_content

logger = structlog.get_logger(__name__)

OLD_PREFIX = "problems/"
NEW_PREFIX = "assets/"


class Command(BaseCommand):
    help = (
        "Move images from media/problems/ to media/assets/ and rebuild local content."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        dry_run = opts["dry_run"]
        media = settings.MEDIA_ROOT

        # 1. Move files and update the stored paths.
        moved = missing = 0
        images = ProblemImage.objects.filter(file__startswith=OLD_PREFIX)
        for image in images:
            old_name = image.file.name
            new_name = NEW_PREFIX + old_name[len(OLD_PREFIX) :]
            old_path, new_path = media / old_name, media / new_name

            if new_path.exists():
                pass  # already moved (or a rerun): just fix the DB path
            elif old_path.exists():
                if not dry_run:
                    new_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(old_path, new_path)
            else:
                missing += 1
                logger.warning("image_file_missing", path=old_name)
                continue  # leave the DB path alone

            if not dry_run:
                image.file.name = new_name
                image.save(update_fields=["file"])
            moved += 1

        self.stdout.write(f"Images relocated: {moved}, missing on disk: {missing}")
        if dry_run:
            self.stdout.write("Dry run: nothing written.")
            return

        # 2. Rebuild every derived content field from the raw HTML.
        rebuilt = 0
        for problem in Problem.objects.exclude(raw_question_html=None).iterator():
            if problem.has_images:
                paths = {
                    i.source_url: i.file.name for i in problem.images.exclude(file="")
                }
                local_html, _ = rewrite_images(
                    problem.raw_question_html or "",
                    lambda idx, src: paths.get(src),
                )
                problem.content_local_html = local_html
                problem.save(update_fields=["content_local_html", "updated_at"])
            build_problem_content(problem)
            rebuilt += 1

        # 3. Tidy up the old, now-empty folders.
        old_root = media / OLD_PREFIX.rstrip("/")
        if old_root.exists():
            for folder in sorted(old_root.glob("*"), reverse=True):
                try:
                    folder.rmdir()
                except OSError:
                    pass  # not empty: leave it
            try:
                old_root.rmdir()
            except OSError:
                pass

        self.stdout.write(
            self.style.SUCCESS(f"Rebuilt content for {rebuilt} problem(s).")
        )
