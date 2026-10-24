from pathlib import Path

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from notes.hashing import content_hash
from notes.models import NoteRecord
from problems.models import Problem


class NoteRecordTests(TestCase):
    def setUp(self):
        self.problem = Problem.objects.create(slug="two-sum", leetcode_id=1)

    def make(self, **overrides) -> NoteRecord:
        values = {
            "problem": self.problem,
            "kind": NoteRecord.Kind.PROBLEM,
            "output_dir": "/vault",
            "relative_path": "Leetcode/problems/0001-two-sum.md",
            "content_hash": content_hash("text"),
            "written_at": timezone.now(),
        }
        return NoteRecord.objects.create(**{**values, **overrides})

    def test_full_path_joins_folder_and_relative_path(self):
        self.assertEqual(
            self.make().full_path, Path("/vault/Leetcode/problems/0001-two-sum.md")
        )

    def test_one_record_per_problem_kind_and_folder(self):
        self.make()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make()

    def test_another_kind_or_folder_is_allowed(self):
        self.make()
        self.make(kind=NoteRecord.Kind.SOLUTION)
        self.make(output_dir="/other-vault")
        self.assertEqual(NoteRecord.objects.count(), 3)

    def test_deleting_the_problem_deletes_its_records(self):
        self.make()
        self.problem.delete()
        self.assertEqual(NoteRecord.objects.count(), 0)
