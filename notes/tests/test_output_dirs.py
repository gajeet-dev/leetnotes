from pathlib import Path

from notes.layout import resolve_output_dirs
from userconfig import update_config
from userconfig.paths import PROJECT_ROOT
from userconfig.tests.helpers import DataDirTestCase


class ResolveOutputDirsTests(DataDirTestCase):
    def test_falls_back_to_default(self):
        self.assertEqual(resolve_output_dirs(), [PROJECT_ROOT / "notes_output"])

    def test_saved_folders_win(self):
        update_config({"output_dirs": ["/saved/a", "/saved/b"]})
        self.assertEqual(resolve_output_dirs(), [Path("/saved/a"), Path("/saved/b")])

    def test_explicit_folders_win_over_everything(self):
        update_config({"output_dirs": ["/saved/a"]})
        self.assertEqual(resolve_output_dirs(["/explicit"]), [Path("/explicit")])

    def test_duplicates_are_removed(self):
        self.assertEqual(
            resolve_output_dirs(["/a", "/a", "/b"]), [Path("/a"), Path("/b")]
        )
