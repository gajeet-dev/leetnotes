import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from notes.backup import backup_note, backup_path, free_path
from notes.layout import BACKUP_FOLDER, ROOT_FOLDER, layout_for


class BackupTests(SimpleTestCase):
    def test_backup_folder_is_hidden_and_inside_the_leetcode_folder(self):
        layout = layout_for(Path("/vault"))
        self.assertEqual(
            layout.backup_dir, Path("/vault") / ROOT_FOLDER / BACKUP_FOLDER
        )
        self.assertTrue(BACKUP_FOLDER.startswith("."))

    def test_backup_path_format(self):
        path = backup_path(
            Path("/b"),
            "two-sum",
            Path("/x/0001-two-sum-solution.md"),
            "20261019-153012",
        )
        self.assertEqual(
            path, Path("/b/two-sum/0001-two-sum-solution-20261019-153012.md")
        )

    def test_free_path_adds_a_counter_when_taken(self):
        with tempfile.TemporaryDirectory() as folder:
            taken = Path(folder) / "note.md"
            taken.write_text("x")
            self.assertEqual(free_path(taken), Path(folder) / "note-2.md")

    def test_backup_copies_and_keeps_the_original(self):
        with tempfile.TemporaryDirectory() as folder:
            note = Path(folder) / "note.md"
            note.write_text("my edits")
            copy = backup_note(
                note, Path(folder) / ".backup", "two-sum", "20261019-153012"
            )
            self.assertEqual(copy.read_text(), "my edits")
            self.assertTrue(note.exists())

    def test_two_backups_in_the_same_second_do_not_clobber(self):
        with tempfile.TemporaryDirectory() as folder:
            note = Path(folder) / "note.md"
            note.write_text("v1")
            first = backup_note(note, Path(folder) / ".backup", "s", "20261019-153012")
            note.write_text("v2")
            second = backup_note(note, Path(folder) / ".backup", "s", "20261019-153012")
            self.assertNotEqual(first, second)
            self.assertEqual((first.read_text(), second.read_text()), ("v1", "v2"))
