from django.test import SimpleTestCase

from pipeline.models import ItemStage, TaskItem
from pipeline.outcomes import DETAIL_LIMIT, done, skipped, waiting


class OutcomeTests(SimpleTestCase):
    def test_done(self):
        outcome = done("fine")
        self.assertEqual(
            (outcome.status, outcome.detail, outcome.input_kind),
            (ItemStage.Status.DONE, "fine", ""),
        )

    def test_skipped(self):
        self.assertEqual(skipped("not needed").status, ItemStage.Status.SKIPPED)

    def test_waiting_says_what_it_waits_for(self):
        outcome = waiting(TaskItem.InputKind.REVIEW, "approve it")
        self.assertEqual(
            (outcome.status, outcome.input_kind, outcome.detail),
            (ItemStage.Status.WAITING, TaskItem.InputKind.REVIEW, "approve it"),
        )

    def test_long_details_are_clipped_to_the_column_size(self):
        detail = done("x" * 500).detail
        self.assertEqual(len(detail), DETAIL_LIMIT)
        self.assertTrue(detail.endswith("…"))

    def test_short_details_are_unchanged(self):
        self.assertEqual(done("x" * DETAIL_LIMIT).detail, "x" * DETAIL_LIMIT)
