import tempfile
from pathlib import Path

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from notes.layout import (
    PROBLEMS_FOLDER,
    ROOT_FOLDER,
    SOLUTIONS_FOLDER,
    ensure_layout,
    layout_for,
)
from notes.services import write_problem_note
from problems.models import Problem, Submission, SyncTask


class LayoutTests(SimpleTestCase):
    def test_paths(self):
        layout = layout_for(Path("/vault"))
        self.assertEqual(
            layout.problems_dir, Path("/vault") / ROOT_FOLDER / PROBLEMS_FOLDER
        )
        self.assertEqual(
            layout.solutions_dir, Path("/vault") / ROOT_FOLDER / SOLUTIONS_FOLDER
        )

    def test_ensure_creates_both_folders_and_is_repeatable(self):
        with tempfile.TemporaryDirectory() as folder:
            layout = ensure_layout(Path(folder))
            ensure_layout(Path(folder))  # second call must not fail
            self.assertTrue(layout.problems_dir.is_dir())
            self.assertTrue(layout.solutions_dir.is_dir())

    def test_missing_output_folder_is_an_error_and_is_not_created(self):
        with tempfile.TemporaryDirectory() as folder:
            missing = Path(folder) / "not-mounted"
            with self.assertRaises(FileNotFoundError):
                ensure_layout(missing)
            self.assertFalse(missing.exists())


class WriteProblemNoteLayoutTests(TestCase):
    def setUp(self):
        self.problem = Problem.objects.create(
            slug="two-sum",
            leetcode_id=1,
            title="Two Sum",
            difficulty="Easy",
            url="https://leetcode.com/problems/two-sum/",
            solved=True,
            tags=[],
            content_local_markdown="Find two numbers.",
        )
        for part in SyncTask.Part.values:
            SyncTask.objects.create(
                problem=self.problem, part=part, status=SyncTask.Status.DONE
            )
        Submission.objects.create(
            problem=self.problem,
            lang="python3",
            code="pass",
            submission_date=timezone.now(),
        )

    def test_problem_note_goes_into_the_problems_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            result = write_problem_note(self.problem, [folder])
            expected = Path(folder) / ROOT_FOLDER / PROBLEMS_FOLDER / "0001-two-sum.md"
            self.assertEqual(result.paths, [expected])
            self.assertTrue(expected.exists())
            self.assertTrue((Path(folder) / ROOT_FOLDER / SOLUTIONS_FOLDER).is_dir())

    def test_missing_output_folder_is_reported_not_created(self):
        result = write_problem_note(self.problem, ["/no/such/folder"])
        self.assertEqual(result.paths, [])
        self.assertIn("/no/such/folder", result.errors)
