from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase

from pipeline.models import ItemStage, Task, TaskItem
from pipeline.stages import STAGE_ORDER, Stage, next_stage, stage_order
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
        self.task = Task.objects.create(name="Run 1")

    def add_item(self, problem, position=0) -> TaskItem:
        return TaskItem.objects.create(
            task=self.task, problem=problem, position=position
        )

    def test_task_defaults(self):
        self.assertEqual(self.task.status, Task.Status.ACTIVE)
        self.assertEqual(self.task.settings, {})

    def test_item_defaults(self):
        item = self.add_item(self.problem)
        self.assertEqual(item.status, TaskItem.Status.QUEUED)
        self.assertEqual((item.stage, item.input_kind, item.error), ("", "", ""))

    def test_a_problem_appears_once_per_task(self):
        self.add_item(self.problem)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.add_item(self.problem, position=1)

    def test_the_same_problem_can_be_in_two_tasks(self):
        self.add_item(self.problem)
        TaskItem.objects.create(task=Task.objects.create(), problem=self.problem)

    def test_items_come_back_in_position_order(self):
        self.add_item(self.other, position=1)
        self.add_item(self.problem, position=0)
        self.assertEqual(
            [item.problem.slug for item in self.task.items.all()],
            ["two-sum", "contains-duplicate"],
        )

    def make_stage_rows(self, item: TaskItem, stages) -> None:
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

    def test_deleting_a_task_deletes_its_items_and_stages(self):
        self.make_stage_rows(self.add_item(self.problem), [Stage.REVIEW])
        self.task.delete()
        self.assertEqual((TaskItem.objects.count(), ItemStage.objects.count()), (0, 0))

    def test_deleting_a_problem_removes_it_from_tasks(self):
        self.add_item(self.problem)
        self.problem.delete()
        self.assertEqual(TaskItem.objects.count(), 0)
