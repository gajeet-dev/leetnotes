import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from notes.models import NoteRecord
from problems.models import Problem, Submission, SyncTask
from problems.services.bundle import export_bundle
from problems.services.snapshots import (
    clear_note_records,
    format_file_size,
    list_snapshots,
    read_manifest_from_archive,
    reset_user_submissions,
)


class SnapshotServicesTests(TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.snapshots_dir = self.temp_dir / "db_snapshots"
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)

        # Create problem record
        # Fetch or create a test problem in the database
        self.problem = Problem.objects.create(
            slug="two-sum",
            leetcode_id=1,
            title="Two Sum",
            solved=True,
        )

        # Create submission record
        # Fetch or create a test submission associated with the problem
        self.submission = Submission.objects.create(
            problem=self.problem,
            lang="python3",
            code="def twoSum(): pass",
            submission_date=timezone.now(),
        )

        # Create submissions sync task
        # Fetch or create a test sync task for the submissions part
        self.task_sub = SyncTask.objects.create(
            problem=self.problem,
            part=SyncTask.Part.SUBMISSIONS,
            status=SyncTask.Status.DONE,
        )

        # Create content sync task
        # Fetch or create a test sync task for the content part
        self.task_content = SyncTask.objects.create(
            problem=self.problem,
            part=SyncTask.Part.CONTENT,
            status=SyncTask.Status.DONE,
        )

        # Create note record
        # Fetch or create a test note record in the database
        self.note_record = NoteRecord.objects.create(
            problem=self.problem,
            kind=NoteRecord.Kind.PROBLEM,
            output_dir="/tmp/vault",
            relative_path="Leetcode/problems/0001-two-sum.md",
            content_hash="abc123hash",
            written_at=timezone.now(),
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_format_file_size(self):
        self.assertEqual(format_file_size(500), "500 B")
        self.assertEqual(format_file_size(2048), "2.0 KB")
        self.assertEqual(format_file_size(5 * 1024 * 1024), "5.0 MB")
        self.assertEqual(format_file_size(3 * 1024 * 1024 * 1024), "3.0 GB")

    def test_reset_user_submissions_preserves_solved_status(self):
        # Verify initial state
        self.assertEqual(Submission.objects.count(), 1)
        self.assertTrue(self.problem.solved)
        self.assertTrue(SyncTask.objects.filter(part=SyncTask.Part.SUBMISSIONS).exists())

        # Execute reset
        result = reset_user_submissions()

        self.assertEqual(result["submissions_deleted"], 1)
        self.assertEqual(result["tasks_deleted"], 1)

        # Submissions table must be empty
        self.assertEqual(Submission.objects.count(), 0)
        # Submissions sync task must be removed
        self.assertFalse(SyncTask.objects.filter(part=SyncTask.Part.SUBMISSIONS).exists())
        # Content sync task must remain
        self.assertTrue(SyncTask.objects.filter(part=SyncTask.Part.CONTENT).exists())

        # Problem solved status must be PRESERVED (still True!)
        self.problem.refresh_from_db()
        self.assertTrue(self.problem.solved)

    def test_clear_note_records_only_deletes_db_records(self):
        self.assertEqual(NoteRecord.objects.count(), 1)

        result = clear_note_records()
        self.assertEqual(result["note_records_deleted"], 1)
        self.assertEqual(NoteRecord.objects.count(), 0)

        # Problem record must be intact
        self.assertEqual(Problem.objects.count(), 1)

    def test_list_snapshots_discovers_and_reads_manifests(self):
        # Export a snapshot bundle into our mock snapshots dir
        bundle_path = self.snapshots_dir / "test_snapshot.tar.gz"
        export_bundle(bundle_path, include_submissions=True, preserve_solved=True)

        with patch("problems.services.snapshots.db_snapshots_dir", return_value=self.snapshots_dir):
            snapshots = list_snapshots()

        self.assertEqual(len(snapshots), 1)
        first_snapshot = snapshots[0]
        self.assertEqual(first_snapshot["filename"], "test_snapshot.tar.gz")
        self.assertEqual(first_snapshot["problems_count"], 1)
        self.assertEqual(first_snapshot["submissions_count"], 1)
        self.assertTrue(first_snapshot["has_submissions"])
        self.assertTrue(first_snapshot["size_bytes"] > 0)

    def test_read_manifest_from_invalid_archive(self):
        corrupt_file = self.snapshots_dir / "corrupt.tar.gz"
        corrupt_file.write_bytes(b"not a valid tar file")
        manifest = read_manifest_from_archive(corrupt_file)
        self.assertIsNone(manifest)
