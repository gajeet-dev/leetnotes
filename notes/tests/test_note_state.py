from notes.layout import PROBLEMS_FOLDER, ROOT_FOLDER, SOLUTIONS_FOLDER
from notes.models import NoteRecord
from notes.state import NoteState, expected_path, inspect_note
from notes.tests.helpers import NoteFilesTestCase


class NoteStateTests(NoteFilesTestCase):
    def state(self) -> NoteState:
        return inspect_note(self.problem, self.kind, self.output).state

    def test_never_written(self):
        self.assertEqual(self.state(), NoteState.NEVER_WRITTEN)

    def test_blank_file_without_a_record_is_never_written(self):
        self.write_file("")  # like the empty file Obsidian makes from a dangling link
        self.assertEqual(self.state(), NoteState.NEVER_WRITTEN)

    def test_file_without_a_record_is_untracked(self):
        self.write_file("an older note")
        self.assertEqual(self.state(), NoteState.UNTRACKED_FILE)

    def test_unchanged(self):
        self.write_file("note text\n")
        self.add_record("note text\n")
        self.assertEqual(self.state(), NoteState.UNCHANGED)

    def test_a_resave_with_different_line_endings_is_still_unchanged(self):
        self.add_record("line one\nline two\n")
        self.write_file("line one  \r\nline two\r\n\r\n")
        self.assertEqual(self.state(), NoteState.UNCHANGED)

    def test_modified(self):
        self.add_record("note text")
        self.write_file("note text, plus my edit")
        self.assertEqual(self.state(), NoteState.MODIFIED)

    def test_missing(self):
        self.add_record("note text")
        self.assertEqual(self.state(), NoteState.MISSING)

    def test_blank_file_with_a_record_is_missing(self):
        self.add_record("note text")
        self.write_file("")
        self.assertEqual(self.state(), NoteState.MISSING)

    def test_expected_paths_per_kind(self):
        problem_path = expected_path(self.problem, NoteRecord.Kind.PROBLEM, self.output)
        solution_path = expected_path(self.problem, NoteRecord.Kind.SOLUTION, self.output)
        self.assertEqual(problem_path.parent, self.output / ROOT_FOLDER / PROBLEMS_FOLDER)
        self.assertEqual(solution_path.parent, self.output / ROOT_FOLDER / SOLUTIONS_FOLDER)
        self.assertTrue(solution_path.name.endswith("-solution.md"))
