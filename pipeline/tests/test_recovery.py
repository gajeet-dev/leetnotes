from pipeline.engine import next_pending_stage
from pipeline.models import ItemStage, Task, TaskItem
from pipeline.outcomes import done
from pipeline.recovery import recover_interrupted
from pipeline.stages import Stage
from pipeline.tests.test_engine import EngineTestCase
from pipeline.transitions import begin_stage, complete_stage

Row = ItemStage.Status
Item = TaskItem.Status


class RecoveryTests(EngineTestCase):
    def setUp(self):
        super().setUp()
        self.problem_a = self.problem("a", 1)
        self.task = self.task_for(self.problem_a, review=False)
        self.item = self.item_of(self.problem_a)

    def reload(self):
        self.item.refresh_from_db()
        self.task.refresh_from_db()

    def test_nothing_to_recover(self):
        self.assertEqual(recover_interrupted(), 0)

    def test_a_stage_that_was_running_is_put_back(self):
        row = next_pending_stage(self.item)
        begin_stage(self.item, row)  # as if the app died during this stage
        self.assertEqual(recover_interrupted(), 1)
        row.refresh_from_db()
        self.reload()
        self.assertEqual((row.status, self.item.status), (Row.PENDING, Item.QUEUED))
        self.assertIsNone(row.started_at)

    def test_an_item_interrupted_between_two_saves_continues_with_its_next_stage(self):
        row = next_pending_stage(self.item)
        begin_stage(self.item, row)
        complete_stage(
            row, done()
        )  # the stage finished, but the item was never queued again
        self.assertEqual(recover_interrupted(), 1)
        self.reload()
        self.assertEqual(
            (self.item.status, self.item.stage), (Item.QUEUED, Stage.FETCH_IMAGES)
        )

    def test_an_item_whose_stages_are_all_finished_is_completed(self):
        self.item.stages.update(status=Row.DONE)
        TaskItem.objects.filter(pk=self.item.pk).update(status=Item.RUNNING)
        self.assertEqual(recover_interrupted(), 1)
        self.reload()
        self.assertEqual(
            (self.item.status, self.task.status), (Item.COMPLETED, Task.Status.FINISHED)
        )

    def test_waiting_failed_and_finished_items_are_left_alone(self):
        for status in (Item.NEEDS_INPUT, Item.FAILED, Item.COMPLETED, Item.QUEUED):
            with self.subTest(status=status):
                TaskItem.objects.filter(pk=self.item.pk).update(status=status)
                self.assertEqual(recover_interrupted(), 0)
                self.reload()
                self.assertEqual(self.item.status, status)

    def test_an_interrupted_task_completes_after_recovery(self):
        begin_stage(self.item, next_pending_stage(self.item))  # the app died here
        recover_interrupted()
        self.run_all()
        self.reload()
        self.assertEqual(
            (self.item.status, self.task.status), (Item.COMPLETED, Task.Status.FINISHED)
        )
        self.assertEqual(self.fetcher.calls[0], ("a", "content"))
        self.assertEqual(
            len([call for call in self.fetcher.calls if call[1] == "content"]), 1
        )
