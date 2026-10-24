import json
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from notes.layout import BACKUP_FOLDER, PROBLEMS_FOLDER, ROOT_FOLDER, SOLUTIONS_FOLDER
from prefill.models import AIResponse
from prefill.services import save_response
from prefill.tests.test_parsing import valid
from problems.models import Problem, Submission, SyncTask


class FakePrompt:
    version = "test"
    input_hash = "test"


def make_ready_problem(slug: str, leetcode_id: int, solved: bool = True) -> Problem:
    problem = Problem.objects.create(
        slug=slug,
        leetcode_id=leetcode_id,
        title=slug,
        difficulty="Easy",
        url=f"https://leetcode.com/problems/{slug}/",
        solved=solved,
        tags=[],
        content_local_markdown="Statement.",
    )
    for part in SyncTask.Part.values:
        SyncTask.objects.create(problem=problem, part=part, status=SyncTask.Status.DONE)
    Submission.objects.create(
        problem=problem, lang="python3", code="pass", submission_date=timezone.now()
    )
    return problem


def add_ai_note(problem: Problem) -> None:
    save_response(
        problem,
        json.dumps(valid()),
        source=AIResponse.Source.MANUAL,
        prompt=FakePrompt(),
    )


class GenerateNotesCommandTests(TestCase):
    def setUp(self):
        self.two_sum = make_ready_problem("two-sum", 1)
        add_ai_note(self.two_sum)
        make_ready_problem("unsolved-one", 2, solved=False)

    def run_command(self, *args) -> str:
        out = StringIO()
        call_command("generate_notes", *args, stdout=out)
        return out.getvalue()

    def problem_note(self, folder: str) -> Path:
        return Path(folder) / ROOT_FOLDER / PROBLEMS_FOLDER / "0001-two-sum.md"

    def solution_note(self, folder: str) -> Path:
        return (
            Path(folder) / ROOT_FOLDER / SOLUTIONS_FOLDER / "0001-two-sum-solution.md"
        )

    def test_default_run_covers_solved_problems_only(self):
        with tempfile.TemporaryDirectory() as folder:
            output = self.run_command("--output", folder)
            self.assertIn("two-sum", output)
            self.assertNotIn("unsolved-one", output)
            self.assertTrue(self.problem_note(folder).exists())
            self.assertTrue(self.solution_note(folder).exists())
            self.assertIn("Done: 1 problem(s)", output)

    def test_limit(self):
        make_ready_problem("add-two-numbers", 3)
        with tempfile.TemporaryDirectory() as folder:
            output = self.run_command("--output", folder, "--limit", "1")
            self.assertIn("two-sum", output)
            self.assertNotIn("add-two-numbers", output)

    def test_unknown_slug(self):
        with self.assertRaisesMessage(CommandError, "No such problem: nope"):
            self.run_command("--slug", "nope")

    def test_replace_edited_needs_a_slug(self):
        with self.assertRaisesMessage(CommandError, "needs --slug"):
            self.run_command("--replace-edited")

    def test_replace_edited_with_slug_backs_up_the_old_notes(self):
        with tempfile.TemporaryDirectory() as folder:
            self.run_command("--output", folder)
            self.problem_note(folder).write_text("my problem edits")
            self.solution_note(folder).write_text("my solution edits")
            output = self.run_command(
                "--output", folder, "--slug", "two-sum", "--replace-edited"
            )
            self.assertIn("problem note: replaced 1; solution note: replaced 1", output)
            backup_dir = Path(folder) / ROOT_FOLDER / BACKUP_FOLDER / "two-sum"
            saved = {path.read_text() for path in backup_dir.glob("*.md")}
            self.assertEqual(saved, {"my problem edits", "my solution edits"})

    def test_edited_notes_are_kept_and_the_hint_is_printed(self):
        with tempfile.TemporaryDirectory() as folder:
            self.run_command("--output", folder)
            self.solution_note(folder).write_text("my edits")
            output = self.run_command("--output", folder)
            self.assertIn("kept edited 1", output)
            self.assertIn("--replace-edited", output)
            self.assertEqual(self.solution_note(folder).read_text(), "my edits")

    def test_write_errors_are_shown_and_fail_the_command(self):
        out = StringIO()
        with self.assertRaisesMessage(CommandError, "had write errors"):
            call_command("generate_notes", "--output", "/no/such/folder", stdout=out)
        self.assertIn("/no/such/folder", out.getvalue())

    def test_no_solved_problems(self):
        Problem.objects.update(solved=False)
        self.assertIn("No problems", self.run_command())
