import json
import tempfile
from pathlib import Path
from unittest import mock

from django.test import TestCase

from notes.layout import BACKUP_FOLDER, ROOT_FOLDER, SOLUTIONS_FOLDER
from notes.solution_notes import write_solution_note
from prefill.models import AIResponse
from prefill.services import save_response
from prefill.tests.test_parsing import valid
from problems.models import Problem


class FakePrompt:
    version = "test"
    input_hash = "test"


class OverwriteSolutionNoteTests(TestCase):
    def setUp(self):
        self.problem = Problem.objects.create(
            slug="course-schedule",
            leetcode_id=207,
            title="Course Schedule",
            difficulty="Medium",
            url="https://leetcode.com/problems/course-schedule/",
            tags=[],
        )
        save_response(
            self.problem,
            json.dumps(valid()),
            source=AIResponse.Source.MANUAL,
            prompt=FakePrompt(),
        )

    def note_path(self, folder: str) -> Path:
        return (
            Path(folder)
            / ROOT_FOLDER
            / SOLUTIONS_FOLDER
            / "0207-course-schedule-solution.md"
        )

    def backup_files(self, folder: str) -> list[Path]:
        return list(
            (Path(folder) / ROOT_FOLDER / BACKUP_FOLDER / "course-schedule").glob(
                "*.md"
            )
        )

    def first_write(self, folder: str, edited_text: str = "my own edits") -> Path:
        write_solution_note(self.problem, [folder])
        path = self.note_path(folder)
        path.write_text(edited_text)
        return path

    def test_overwrite_backs_up_the_old_note_then_replaces_it(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.first_write(folder)
            result = write_solution_note(self.problem, [folder], replace_edited=True)
            self.assertEqual(result.written, [path])
            self.assertTrue(path.read_text().startswith("---\n"))
            backups = self.backup_files(folder)
            self.assertEqual(result.backups, backups)
            self.assertEqual(backups[0].read_text(), "my own edits")
            self.assertTrue(
                backups[0].name.startswith("0207-course-schedule-solution-")
            )

    def test_without_overwrite_nothing_is_backed_up_or_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.first_write(folder)
            result = write_solution_note(self.problem, [folder])
            self.assertEqual(result.existing, [path])
            self.assertEqual(path.read_text(), "my own edits")
            self.assertEqual(self.backup_files(folder), [])

    def test_blank_file_is_written_without_overwrite_and_without_a_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.first_write(
                folder, edited_text=""
            )  # like Obsidian's empty file
            result = write_solution_note(self.problem, [folder])
            self.assertEqual(result.written, [path])
            self.assertTrue(path.read_text().startswith("---\n"))
            self.assertEqual(self.backup_files(folder), [])

    def test_failed_backup_leaves_the_note_untouched(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.first_write(folder)
            with mock.patch(
                "notes.writer.backup_note", side_effect=OSError("disk full")
            ):
                result = write_solution_note(self.problem, [folder], replace_edited=True)
            self.assertEqual(path.read_text(), "my own edits")
            self.assertEqual(result.written, [])
            self.assertIn(folder, result.errors)
