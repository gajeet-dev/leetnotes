import os
import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase, TestCase

from userconfig.paths import DATA_DIR_ENV, config_path


class DataDirTestCase(SimpleTestCase):
    """Points the data folder at a temporary directory that doesn't exist yet."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.data_dir = Path(folder.name) / "data"
        patcher = mock.patch.dict(os.environ, {DATA_DIR_ENV: str(self.data_dir)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_raw(self, text: str) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        config_path().write_text(text)


class DataDirDBTestCase(DataDirTestCase, TestCase):
    """A temporary data folder, plus database access."""
