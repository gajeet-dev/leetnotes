import json
import tempfile
from pathlib import Path

from django.test import TestCase

from notes.layout import ROOT_FOLDER, SOLUTIONS_FOLDER
from notes.solution_notes import write_solution_note
from prefill.models import AIResponse
from prefill.services import save_response
from prefill.tests.test_parsing import valid
from problems.models import Problem


class FakePrompt:
    version = "test"
    input_hash = "test"


class WriteSolutionNoteTests(TestCase):
    def setUp(self):
        self.problem = Problem.objects.create(
            slug="course-schedule",
            leetcode_id=207,
            title="Course Schedule",
            difficulty="Medium",
            url="https://leetcode.com/problems/course-schedule/",
            tags=[{"name": "Graph", "slug": "graph"}],
        )
        save_response(
            self.problem,
            json.dumps(valid()),
            source=AIResponse.Source.MANUAL,
            prompt=FakePrompt(),
        )

    def expected_path(self, folder: str) -> Path:
        return (
            Path(folder)
            / ROOT_FOLDER
            / SOLUTIONS_FOLDER
            / "0207-course-schedule-solution.md"
        )

    def test_writes_into_the_solutions_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            result = write_solution_note(self.problem, [folder])
            self.assertEqual(result.written, [self.expected_path(folder)])
            self.assertTrue(result.written[0].read_text().startswith("---\n"))

    def test_never_overwrites_an_existing_note(self):
        with tempfile.TemporaryDirectory() as folder:
            write_solution_note(self.problem, [folder])
            path = self.expected_path(folder)
            path.write_text("my own edits")
            result = write_solution_note(self.problem, [folder])
            self.assertEqual(result.existing, [path])
            self.assertEqual(result.written, [])
            self.assertEqual(path.read_text(), "my own edits")

    def test_one_bad_folder_does_not_stop_the_others(self):
        with tempfile.TemporaryDirectory() as folder:
            result = write_solution_note(self.problem, ["/no/such/folder", folder])
            self.assertEqual(result.written, [self.expected_path(folder)])
            self.assertIn("/no/such/folder", result.errors)

    def test_writes_into_every_folder(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            result = write_solution_note(self.problem, [a, b])
            self.assertEqual(len(result.written), 2)

    def test_skipped_without_an_active_note(self):
        AIResponse.objects.all().update(is_active=False)
        with tempfile.TemporaryDirectory() as folder:
            result = write_solution_note(self.problem, [folder])
            self.assertEqual(result.skipped_reason, "no active AI note")
            self.assertFalse(self.expected_path(folder).exists())
