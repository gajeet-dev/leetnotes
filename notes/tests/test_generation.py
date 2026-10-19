import json
import tempfile
from pathlib import Path

from django.test import TestCase
from django.utils import timezone

from notes.generation import (
    describe_problem_note,
    describe_result,
    describe_solution_note,
    generate_notes,
    has_errors,
)
from notes.layout import PROBLEMS_FOLDER, ROOT_FOLDER, SOLUTIONS_FOLDER
from prefill.models import AIResponse
from prefill.services import save_response
from prefill.tests.test_parsing import valid
from problems.models import Problem, Submission, SyncTask


class FakePrompt:
    version = "test"
    input_hash = "test"


class GenerateNotesTests(TestCase):
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

    def add_ai_note(self):
        save_response(
            self.problem,
            json.dumps(valid()),
            source=AIResponse.Source.MANUAL,
            prompt=FakePrompt(),
        )

    def problem_path(self, folder: str) -> Path:
        return Path(folder) / ROOT_FOLDER / PROBLEMS_FOLDER / "0001-two-sum.md"

    def solution_path(self, folder: str) -> Path:
        return (
            Path(folder) / ROOT_FOLDER / SOLUTIONS_FOLDER / "0001-two-sum-solution.md"
        )

    def test_writes_both_notes(self):
        self.add_ai_note()
        with tempfile.TemporaryDirectory() as folder:
            result = generate_notes(self.problem, [folder])
            self.assertEqual(result.problem_note.paths, [self.problem_path(folder)])
            self.assertEqual(result.solution_note.written, [self.solution_path(folder)])
            self.assertFalse(has_errors(result))

    def test_problem_note_is_written_even_without_an_ai_note(self):
        with tempfile.TemporaryDirectory() as folder:
            result = generate_notes(self.problem, [folder])
            self.assertTrue(self.problem_path(folder).exists())
            self.assertEqual(result.solution_note.skipped_reason, "no active AI note")

    def test_solution_note_is_written_even_if_the_problem_note_is_skipped(self):
        self.add_ai_note()
        self.problem.solved = False
        self.problem.save()
        with tempfile.TemporaryDirectory() as folder:
            result = generate_notes(self.problem, [folder])
            self.assertEqual(result.problem_note.skipped_reason, "not solved")
            self.assertTrue(self.solution_path(folder).exists())

    def test_overwrite_flag_reaches_the_solution_writer(self):
        self.add_ai_note()
        with tempfile.TemporaryDirectory() as folder:
            generate_notes(self.problem, [folder])
            self.solution_path(folder).write_text("my edits")

            kept = generate_notes(self.problem, [folder])
            self.assertEqual(len(kept.solution_note.existing), 1)
            self.assertEqual(self.solution_path(folder).read_text(), "my edits")

            replaced = generate_notes(self.problem, [folder], overwrite_solution=True)
            self.assertEqual(len(replaced.solution_note.backups), 1)
            self.assertTrue(self.solution_path(folder).read_text().startswith("---\n"))

    def test_a_bad_folder_is_reported(self):
        self.add_ai_note()
        result = generate_notes(self.problem, ["/no/such/folder"])
        self.assertTrue(has_errors(result))

    def test_descriptions(self):
        self.add_ai_note()
        with tempfile.TemporaryDirectory() as folder:
            result = generate_notes(self.problem, [folder])
            self.assertEqual(
                describe_problem_note(result.problem_note), "problem note: written 1"
            )
            self.assertEqual(
                describe_solution_note(result.solution_note), "solution note: written 1"
            )
            self.assertEqual(
                describe_result(result),
                "two-sum: problem note: written 1; solution note: written 1",
            )

    def test_skipped_description(self):
        with tempfile.TemporaryDirectory() as folder:
            result = generate_notes(self.problem, [folder])
            self.assertEqual(
                describe_solution_note(result.solution_note),
                "solution note skipped (no active AI note)",
            )
