from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from problems.services.bundle import DEFAULT_BUNDLE_FILENAME, export_bundle


class Command(BaseCommand):
    help = (
        "Extract problems, problem images, content/images sync tasks, and media files "
        "into a distributable bundle archive (.tar.gz). Does not alter current database data."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "-o",
            "--output",
            type=str,
            default=None,
            help=f"Target path for the bundle archive. Defaults to {DEFAULT_BUNDLE_FILENAME} in BASE_DIR.",
        )
        parser.add_argument(
            "--preserve-solved",
            action="store_true",
            help="Preserve current solved=True in exported problems (default is False for clean public bundles).",
        )
        parser.add_argument(
            "--include-submissions",
            action="store_true",
            help="Include submission sync tasks in export (default is False).",
        )
        parser.add_argument(
            "--no-ai-notes",
            action="store_true",
            help="Exclude AI editorial solution notes from the export (included by default).",
        )
        parser.add_argument(
            "--include-note-records",
            action="store_true",
            help="Include NoteRecord tracking records from database in the export (default is False).",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Export complete database: includes submissions, AI notes, note records, and preserves solved status.",
        )

    def handle(self, *args, **opts):
        output_arg = opts["output"]
        if output_arg:
            target_path = Path(output_arg)
        else:
            target_path = Path(settings.BASE_DIR) / DEFAULT_BUNDLE_FILENAME

        self.stdout.write(f"Exporting problems bundle to: {target_path}")

        export_all = opts.get("all", False)
        include_submissions = opts["include_submissions"] or export_all
        include_ai_notes = not opts["no_ai_notes"] or export_all
        preserve_solved = opts["preserve_solved"] or include_submissions or export_all
        include_note_records = opts["include_note_records"] or export_all

        try:
            result = export_bundle(
                output_path=target_path,
                include_submissions=include_submissions,
                include_ai_notes=include_ai_notes,
                preserve_solved=preserve_solved,
                include_note_records=include_note_records,
                stdout=self.stdout,
            )
        except Exception as exc:
            raise CommandError(f"Failed to export bundle: {exc}") from exc

        size_mb = result.bundle_size_bytes / (1024 * 1024)
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("--- Export Summary ---"))
        self.stdout.write(f"Problems:     {result.problems_count}")
        self.stdout.write(f"Images:       {result.images_count}")
        self.stdout.write(f"Sync Tasks:   {result.tasks_count}")
        self.stdout.write(f"Submissions:  {result.submissions_count}")
        self.stdout.write(f"AI Notes:     {result.ai_responses_count}")
        self.stdout.write(f"Note Records: {result.note_records_count}")
        self.stdout.write(f"Media Files: {result.media_files_count} packaged")
        if result.missing_media_count:
            self.stdout.write(
                self.style.WARNING(f"Missing:    {result.missing_media_count} media files missing on disk")
            )
        self.stdout.write(f"Archive Size: {size_mb:.2f} MB")
        self.stdout.write(
            self.style.SUCCESS(f"\nBundle successfully exported to {result.output_path}")
        )
