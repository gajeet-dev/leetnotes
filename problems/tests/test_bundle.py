import io
import json
import shutil
import tarfile
import tempfile
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase, override_settings

from problems.models import Problem, ProblemImage, Submission, SyncTask
from problems.services.bundle import export_bundle, import_bundle


class BundleTests(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.media_dir = Path(self.temp_dir) / "media"
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.output_bundle = Path(self.temp_dir) / "test_bundle.tar.gz"

        # Create dummy media file
        sample_img_dir = self.media_dir / "assets" / "two-sum"
        sample_img_dir.mkdir(parents=True, exist_ok=True)
        self.sample_img_file = sample_img_dir / "0.png"
        self.sample_img_file.write_bytes(b"FAKE_PNG_DATA")

        # Create problem with image, submissions, and tasks
        self.problem = Problem.objects.create(
            slug="two-sum",
            leetcode_id=1,
            title="Two Sum",
            difficulty=Problem.Difficulty.EASY,
            category=Problem.Category.ALGORITHMS,
            tags=[{"name": "Array", "slug": "array"}],
            solved=True,
            paid_only=False,
            raw_question_html="<p>Two Sum</p>",
            content_local_markdown="Two Sum description",
            has_images=True,
        )

        self.problem_image = ProblemImage.objects.create(
            problem=self.problem,
            source_url="https://leetcode.com/two-sum/0.png",
            file="assets/two-sum/0.png",
        )

        self.task_content = SyncTask.objects.create(
            problem=self.problem,
            part=SyncTask.Part.CONTENT,
            status=SyncTask.Status.DONE,
            attempts=1,
        )

        self.task_images = SyncTask.objects.create(
            problem=self.problem,
            part=SyncTask.Part.IMAGES,
            status=SyncTask.Status.DONE,
            attempts=1,
        )

        self.task_submissions = SyncTask.objects.create(
            problem=self.problem,
            part=SyncTask.Part.SUBMISSIONS,
            status=SyncTask.Status.DONE,
            attempts=1,
        )

        self.submission = Submission.objects.create(
            problem=self.problem,
            lang="python3",
            code="def twoSum(): pass",
            submission_date="2026-10-01 10:00:00+00:00",
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_export_excludes_submissions_and_sanitizes_solved(self):
        with override_settings(MEDIA_ROOT=self.media_dir):
            result = export_bundle(self.output_bundle, preserve_solved=False)

        self.assertTrue(self.output_bundle.exists())
        self.assertEqual(result.problems_count, 1)
        self.assertEqual(result.images_count, 1)
        # Should only have content and images, NOT submissions
        self.assertEqual(result.tasks_count, 2)
        self.assertEqual(result.media_files_count, 1)
        self.assertEqual(result.missing_media_count, 0)

        # Inspect the contents of the archive
        with tarfile.open(self.output_bundle, "r:gz") as tar:
            names = tar.getnames()
            self.assertIn("manifest.json", names)
            self.assertIn("data.json", names)
            self.assertIn("media/assets/two-sum/0.png", names)

            # Check data.json contents
            data_file = tar.extractfile("data.json")
            self.assertIsNotNone(data_file)
            data = json.load(data_file)

            problems = data["problems"]
            self.assertEqual(len(problems), 1)
            self.assertEqual(problems[0]["slug"], "two-sum")
            # Solved should be False for public bundles
            self.assertFalse(problems[0]["solved"])

            tasks = data["tasks"]
            self.assertEqual(len(tasks), 2)
            parts = {t["part"] for t in tasks}
            self.assertEqual(parts, {"content", "images"})
            self.assertNotIn("submissions", parts)

    def test_export_preserve_solved_flag(self):
        with override_settings(MEDIA_ROOT=self.media_dir):
            export_bundle(self.output_bundle, preserve_solved=True)

        with tarfile.open(self.output_bundle, "r:gz") as tar:
            data_file = tar.extractfile("data.json")
            data = json.load(data_file)
            self.assertTrue(data["problems"][0]["solved"])

    def test_import_bundle_populates_database_and_media(self):
        # First export
        with override_settings(MEDIA_ROOT=self.media_dir):
            export_bundle(self.output_bundle, preserve_solved=False)

        # Now clean DB and test target media folder
        target_media = Path(self.temp_dir) / "target_media"
        SyncTask.objects.all().delete()
        ProblemImage.objects.all().delete()
        Submission.objects.all().delete()
        Problem.objects.all().delete()

        with override_settings(MEDIA_ROOT=target_media):
            result = import_bundle(self.output_bundle)

            self.assertEqual(result.problems_created, 1)
            self.assertEqual(result.images_created, 1)
            self.assertEqual(result.tasks_created, 2)
            self.assertEqual(result.media_extracted, 1)

            # Verify problem
            imported_prob = Problem.objects.get(slug="two-sum")
            self.assertEqual(imported_prob.leetcode_id, 1)
            self.assertFalse(imported_prob.solved)

            # Verify image
            imported_img = ProblemImage.objects.get(problem=imported_prob)
            self.assertEqual(imported_img.file.name, "assets/two-sum/0.png")

            # Verify tasks: submissions task should NOT exist
            self.assertEqual(SyncTask.objects.filter(problem=imported_prob).count(), 2)
            self.assertFalse(
                SyncTask.objects.filter(
                    problem=imported_prob, part=SyncTask.Part.SUBMISSIONS
                ).exists()
            )

            # Verify media file on disk
            imported_file = target_media / "assets" / "two-sum" / "0.png"
            self.assertTrue(imported_file.exists())
            self.assertEqual(imported_file.read_bytes(), b"FAKE_PNG_DATA")

    def test_import_preserves_existing_user_solved_and_submissions(self):
        # Problem is solved in destination DB with a submission
        existing_problem = Problem.objects.get(slug="two-sum")
        existing_problem.solved = True
        existing_problem.save()

        # Bundle has solved=False
        with override_settings(MEDIA_ROOT=self.media_dir):
            export_bundle(self.output_bundle, preserve_solved=False)

        # Re-import should NOT reset solved to False on existing solved problem
        target_media = Path(self.temp_dir) / "target_media"
        with override_settings(MEDIA_ROOT=target_media):
            import_bundle(self.output_bundle)

            reloaded_prob = Problem.objects.get(slug="two-sum")
            self.assertTrue(reloaded_prob.solved)

            # Submission should remain intact
            self.assertEqual(Submission.objects.filter(problem=reloaded_prob).count(), 1)
            # Submission task should remain intact
            self.assertTrue(
                SyncTask.objects.filter(
                    problem=reloaded_prob, part=SyncTask.Part.SUBMISSIONS
                ).exists()
            )

    def test_import_dry_run_does_not_modify_anything(self):
        with override_settings(MEDIA_ROOT=self.media_dir):
            export_bundle(self.output_bundle, preserve_solved=False)

        # Clean DB
        SyncTask.objects.all().delete()
        ProblemImage.objects.all().delete()
        Submission.objects.all().delete()
        Problem.objects.all().delete()

        target_media = Path(self.temp_dir) / "target_media"
        with override_settings(MEDIA_ROOT=target_media):
            result = import_bundle(self.output_bundle, dry_run=True)

            self.assertTrue(result.dry_run)
            self.assertEqual(Problem.objects.count(), 0)
            self.assertEqual(ProblemImage.objects.count(), 0)
            self.assertEqual(SyncTask.objects.count(), 0)
            self.assertFalse(target_media.exists())

    def test_management_commands_execution(self):
        stdout = io.StringIO()
        with override_settings(MEDIA_ROOT=self.media_dir):
            call_command(
                "export_bundle",
                output=str(self.output_bundle),
                stdout=stdout,
            )
            self.assertTrue(self.output_bundle.exists())
            self.assertIn("Bundle successfully exported", stdout.getvalue())

            import_stdout = io.StringIO()
            call_command(
                "import_bundle",
                str(self.output_bundle),
                dry_run=True,
                stdout=import_stdout,
            )
            self.assertIn("Dry run completed successfully", import_stdout.getvalue())
