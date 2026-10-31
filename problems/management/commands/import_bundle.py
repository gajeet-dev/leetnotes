from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from problems.services.bundle import DEFAULT_BUNDLE_FILENAME, import_bundle


class Command(BaseCommand):
    help = (
        "Import problems, problem images, and content/image sync tasks from a bundle archive. "
        "Also extracts bundled media assets into MEDIA_ROOT."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "bundle",
            nargs="?",
            type=str,
            default=None,
            help=f"Path to bundle (.tar.gz, .zip, or directory). Defaults to {DEFAULT_BUNDLE_FILENAME}",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simulate the import without writing any database records or media files.",
        )
        parser.add_argument(
            "--no-media",
            action="store_true",
            help="Skip extracting media asset files to disk.",
        )
        parser.add_argument(
            "--no-tasks",
            action="store_true",
            help="Skip importing sync tasks.",
        )
        parser.add_argument(
            "--preserve-solved",
            action="store_true",
            help="Preserve solved=True from the bundle (by default, imported problems default to solved=False).",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="Batch size for database bulk operations (default 500).",
        )

    def handle(self, *args, **opts):
        bundle_arg = opts["bundle"]
        if bundle_arg:
            bundle_path = Path(bundle_arg)
        else:
            bundle_path = Path(settings.BASE_DIR) / DEFAULT_BUNDLE_FILENAME

        if not bundle_path.exists():
            raise CommandError(
                f"Bundle file not found at {bundle_path}. "
                f"Please specify a path or run 'python manage.py export_bundle' first."
            )

        self.stdout.write(f"Importing bundle from: {bundle_path}")
        if opts["dry_run"]:
            self.stdout.write(self.style.WARNING("DRY RUN: No changes will be written."))

        try:
            result = import_bundle(
                bundle_path=bundle_path,
                dry_run=opts["dry_run"],
                no_media=opts["no_media"],
                no_tasks=opts["no_tasks"],
                reset_solved=not opts["preserve_solved"],
                batch_size=opts["batch_size"],
                stdout=self.stdout,
            )
        except Exception as exc:
            raise CommandError(f"Failed to import bundle: {exc}") from exc

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("--- Import Summary ---"))
        self.stdout.write(
            f"Problems:    {result.problems_created} created, {result.problems_updated} updated"
        )
        self.stdout.write(
            f"Images:      {result.images_created} created, {result.images_existing} already present"
        )
        self.stdout.write(
            f"Sync Tasks:  {result.tasks_created} created, {result.tasks_updated} updated, "
            f"{result.tasks_existing} already present"
        )
        if not opts["no_media"]:
            self.stdout.write(
                f"Media Files: {result.media_extracted} extracted, {result.media_skipped} already up-to-date"
            )

        if opts["dry_run"]:
            self.stdout.write(
                self.style.WARNING("\nDry run completed successfully. No changes applied.")
            )
        else:
            self.stdout.write(
                self.style.SUCCESS("\nBundle imported successfully!")
            )
