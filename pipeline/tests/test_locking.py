import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from pipeline.locking import FileLock


class FileLockTests(SimpleTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "sub" / "worker.lock"

    def test_the_first_lock_wins_and_the_folder_is_created(self):
        first = FileLock(self.path)
        self.assertTrue(first.acquire())
        self.addCleanup(first.release)
        self.assertTrue(self.path.exists())

    def test_a_second_lock_on_the_same_file_is_refused_even_in_one_process(self):
        first, second = FileLock(self.path), FileLock(self.path)
        self.assertTrue(first.acquire())
        self.addCleanup(first.release)
        self.assertFalse(second.acquire())

    def test_releasing_lets_another_lock_in(self):
        first, second = FileLock(self.path), FileLock(self.path)
        first.acquire()
        first.release()
        self.assertTrue(second.acquire())
        self.addCleanup(second.release)

    def test_releasing_without_holding_is_harmless(self):
        FileLock(self.path).release()
