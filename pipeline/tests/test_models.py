from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase

from pipeline.models import ItemStage, PipelineItem
from pipeline.stages.definitions import STAGE_ORDER, Stage, next_stage, stage_order
from problems.models import Problem


class StageTests(SimpleTestCase):
    def test_order_is_the_pipeline_order(self):
        self.assertEqual(
            [stage.value for stage in STAGE_ORDER],
            [
                "fetch_problem",
                "fetch_images",
                "fetch_submission",
                "generate_prefill",
                "review",
                "save_notes",
            ],
        )

    def test_labels_are_the_working_on_text(self):
        self.assertEqual(Stage.FETCH_IMAGES.label, "Fetching images")

    def test_stage_order_and_next_stage(self):
        self.assertEqual(stage_order("review"), 4)
        self.assertEqual(next_stage(Stage.FETCH_PROBLEM), Stage.FETCH_IMAGES)
        self.assertIsNone(next_stage(Stage.SAVE_NOTES))


class PipelineModelTests(TestCase):
    def setUp(self):
        self.problem = Problem.objects.create(slug="two-sum", leetcode_id=1)
        self.other = Problem.objects.create(slug="contains-duplicate", leetcode_id=217)

    def add_item(self, problem, position=0) -> PipelineItem:
        return PipelineItem.objects.create(problem=problem, position=position)

    def test_item_defaults(self):
        item = self.add_item(self.problem)
        self.assertEqual(item.status, PipelineItem.Status.QUEUED)
        self.assertEqual((item.stage, item.input_kind, item.error), ("", "", ""))
        self.assertEqual(item.settings, {})

    def test_multiple_pipeline_runs_for_problem(self):
        self.add_item(self.problem, position=0)
        self.add_item(self.problem, position=1)
        self.assertEqual(self.problem.pipeline_items.count(), 2)

    def make_stage_rows(self, item: PipelineItem, stages) -> None:
        for stage in stages:
            ItemStage.objects.create(item=item, stage=stage, order=stage_order(stage))

    def test_stages_come_back_in_pipeline_order(self):
        item = self.add_item(self.problem)
        self.make_stage_rows(item, reversed(STAGE_ORDER))
        self.assertEqual(
            [row.stage for row in item.stages.all()], [s.value for s in STAGE_ORDER]
        )

    def test_a_stage_appears_once_per_item(self):
        item = self.add_item(self.problem)
        self.make_stage_rows(item, [Stage.REVIEW])
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make_stage_rows(item, [Stage.REVIEW])

    def test_deleting_an_item_deletes_its_stages(self):
        item = self.add_item(self.problem)
        self.make_stage_rows(item, [Stage.REVIEW])
        item.delete()
        self.assertEqual(
            (PipelineItem.objects.count(), ItemStage.objects.count()), (0, 0)
        )

    def test_deleting_a_problem_removes_its_items(self):
        self.add_item(self.problem)
        self.problem.delete()
        self.assertEqual(PipelineItem.objects.count(), 0)
