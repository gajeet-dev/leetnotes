from pathlib import Path

from django.test import override_settings

from notes.layout import resolve_output_dirs
from userconfig import update_config
from userconfig.tests.helpers import DataDirTestCase


@override_settings(NOTES_OUTPUT_DIRS=[Path("/env/vault")])
class ResolveOutputDirsTests(DataDirTestCase):
    def test_falls_back_to_env(self):
        self.assertEqual(resolve_output_dirs(), [Path("/env/vault")])

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
