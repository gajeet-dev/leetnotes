import tempfile
from pathlib import Path

from django.test import TestCase
from django.utils import timezone

from notes.hashing import content_hash
from notes.models import NoteRecord
from notes.state import dir_key, expected_path
from problems.models import Problem


class NoteFilesTestCase(TestCase):
    """A problem, a temporary output folder, and helpers to stage a note's file and record."""

    kind = NoteRecord.Kind.PROBLEM

    def setUp(self):
        self.problem = Problem.objects.create(slug="two-sum", leetcode_id=1)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.output = Path(folder.name)
        self.path = expected_path(self.problem, self.kind, self.output)

    def write_file(self, text: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(text)

    def add_record(self, text: str) -> NoteRecord:
        return NoteRecord.objects.create(
            problem=self.problem,
            kind=self.kind,
            output_dir=dir_key(self.output),
            relative_path=str(self.path.relative_to(self.output)),
            content_hash=content_hash(text),
            written_at=timezone.now(),
        )
