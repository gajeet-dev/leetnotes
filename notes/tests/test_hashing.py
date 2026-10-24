import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from notes.hashing import content_hash, file_hash, normalize_note_text


class HashingTests(SimpleTestCase):
    def test_line_endings_do_not_matter(self):
        self.assertEqual(content_hash("a\r\nb\r\n"), content_hash("a\nb\n"))

    def test_trailing_spaces_and_final_newlines_do_not_matter(self):
        self.assertEqual(content_hash("a  \nb\t\n\n\n"), content_hash("a\nb"))

    def test_leading_indentation_still_matters(self):
        self.assertNotEqual(content_hash("  a"), content_hash("a"))

    def test_a_real_edit_changes_the_hash(self):
        self.assertNotEqual(content_hash("a\nb"), content_hash("a\nB"))

    def test_an_extra_blank_line_in_the_middle_is_an_edit(self):
        self.assertNotEqual(content_hash("a\nb"), content_hash("a\n\nb"))

    def test_hash_is_sha256_hex(self):
        self.assertEqual(len(content_hash("x")), 64)

    def test_normalize_keeps_the_content(self):
        self.assertEqual(normalize_note_text("a \r\n\r\nb  \n"), "a\n\nb")

    def test_file_hash_matches_content_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "note.md"
            path.write_text("hello\nworld\n")
            self.assertEqual(file_hash(path), content_hash("hello\nworld"))
