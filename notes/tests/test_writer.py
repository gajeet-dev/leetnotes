from unittest import mock

from notes.hashing import content_hash
from notes.layout import layout_for
from notes.models import NoteRecord
from notes.policy import NoteAction
from notes.state import dir_key
from notes.tests.helpers import NoteFilesTestCase
from notes.writer import write_note


class WriteNoteTests(NoteFilesTestCase):
    def write(self, text, **options):
        return write_note(self.problem, self.kind, self.output, text, **options)

    def record(self):
        return NoteRecord.objects.filter(
            problem=self.problem, kind=self.kind, output_dir=dir_key(self.output)
        ).first()

    def backups(self):
        folder = layout_for(self.output).backup_dir / self.problem.slug
        return list(folder.glob("*.md"))

    def test_new_note_is_written_and_recorded(self):
        outcome = self.write("fresh text")
        self.assertEqual((outcome.action, outcome.wrote), (NoteAction.WRITE, True))
        self.assertEqual(outcome.path, self.path)
        self.assertEqual(self.path.read_text(), "fresh text")
        self.assertEqual(self.record().content_hash, content_hash("fresh text"))
        self.assertEqual(self.record().relative_path, str(self.path.relative_to(self.output)))

    def test_unchanged_note_is_overwritten_without_a_backup(self):
        self.write_file("old")
        self.add_record("old")
        outcome = self.write("new")
        self.assertEqual(outcome.action, NoteAction.OVERWRITE)
        self.assertEqual(self.path.read_text(), "new")
        self.assertEqual(self.record().content_hash, content_hash("new"))
        self.assertEqual(self.backups(), [])

    def test_identical_text_is_left_alone(self):
        self.write_file("same\n")
        self.add_record("same\n")
        outcome = self.write("same")
        self.assertEqual((outcome.action, outcome.wrote), (NoteAction.UP_TO_DATE, False))
        self.assertEqual(self.path.read_text(), "same\n")

    def test_edited_note_is_skipped(self):
        self.write_file("my edits")
        self.add_record("what we wrote")
        outcome = self.write("new")
        self.assertEqual((outcome.action, outcome.wrote), (NoteAction.SKIP_EDITED, False))
        self.assertEqual(self.path.read_text(), "my edits")
        self.assertEqual(self.record().content_hash, content_hash("what we wrote"))

    def test_edited_note_replaced_on_request_is_backed_up_first(self):
        self.write_file("my edits")
        self.add_record("what we wrote")
        outcome = self.write("new", replace_edited=True)
        self.assertEqual(outcome.action, NoteAction.BACKUP_AND_OVERWRITE)
        self.assertEqual(self.backups(), [outcome.backup])
        self.assertEqual(outcome.backup.read_text(), "my edits")
        self.assertEqual(self.path.read_text(), "new")
        self.assertEqual(self.record().content_hash, content_hash("new"))

    def test_older_note_that_already_matches_is_adopted(self):
        self.write_file("identical")
        outcome = self.write("identical")
        self.assertEqual((outcome.action, outcome.wrote), (NoteAction.ADOPT, False))
        self.assertEqual(self.path.read_text(), "identical")
        self.assertEqual(self.record().content_hash, content_hash("identical"))

    def test_older_note_that_differs_is_protected_and_not_recorded(self):
        self.write_file("older text")
        outcome = self.write("new text")
        self.assertEqual(outcome.action, NoteAction.SKIP_EDITED)
        self.assertEqual(self.path.read_text(), "older text")
        self.assertIsNone(self.record())

    def test_missing_note_is_written_again(self):
        self.add_record("gone")
        outcome = self.write("fresh")
        self.assertEqual(outcome.action, NoteAction.WRITE)
        self.assertEqual(self.path.read_text(), "fresh")
        self.assertEqual(self.record().content_hash, content_hash("fresh"))

    def test_failed_backup_leaves_the_note_and_record_untouched(self):
        self.write_file("my edits")
        self.add_record("old")
        with mock.patch("notes.writer.backup_note", side_effect=OSError("disk full")):
            outcome = self.write("new", replace_edited=True)
        self.assertIn("disk full", outcome.error)
        self.assertIsNone(outcome.action)
        self.assertEqual(self.path.read_text(), "my edits")
        self.assertEqual(self.record().content_hash, content_hash("old"))

    def test_missing_output_folder_is_an_error_and_nothing_is_recorded(self):
        outcome = write_note(self.problem, self.kind, "/no/such/folder", "x")
        self.assertIsNotNone(outcome.error)
        self.assertFalse(outcome.wrote)
        self.assertEqual(NoteRecord.objects.count(), 0)


class SolutionWriteNoteTests(WriteNoteTests):
    """The same behaviour for solution notes."""

    kind = NoteRecord.Kind.SOLUTION
