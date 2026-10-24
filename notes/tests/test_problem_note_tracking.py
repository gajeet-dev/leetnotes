import tempfile
from pathlib import Path

from django.test import TestCase
from django.utils import timezone

from notes.models import NoteRecord
from notes.services import render_problem_note, write_problem_note
from notes.state import expected_path
from problems.models import Problem, Submission, SyncTask


class TrackedProblemNoteTests(TestCase):
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
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.output = Path(folder.name)
        self.path = expected_path(self.problem, NoteRecord.Kind.PROBLEM, self.output)

    def write(self, **options):
        return write_problem_note(self.problem, [self.output], **options)

    def test_a_second_run_leaves_an_unchanged_note_alone(self):
        self.assertEqual(self.write().written, [self.path])
        second = self.write()
        self.assertEqual((second.written, second.existing), ([], [self.path]))

    def test_your_edits_survive_a_rerun(self):
        self.write()
        self.path.write_text("my own notes")
        result = self.write()
        self.assertEqual(result.existing, [self.path])
        self.assertEqual(self.path.read_text(), "my own notes")

    def test_replace_edited_backs_up_then_rewrites(self):
        self.write()
        self.path.write_text("my own notes")
        result = self.write(replace_edited=True)
        self.assertEqual(len(result.backups), 1)
        self.assertEqual(result.backups[0].read_text(), "my own notes")
        self.assertTrue(self.path.read_text().startswith("---\n"))

    def test_an_identical_note_from_before_tracking_is_adopted(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(render_problem_note(self.problem))
        result = self.write()
        self.assertEqual((result.written, result.existing), ([], [self.path]))
        self.assertEqual(NoteRecord.objects.filter(problem=self.problem).count(), 1)

    def test_a_different_note_from_before_tracking_is_protected(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("older template")
        result = self.write()
        self.assertEqual(result.existing, [self.path])
        self.assertEqual(self.path.read_text(), "older template")
        self.assertEqual(NoteRecord.objects.count(), 0)
