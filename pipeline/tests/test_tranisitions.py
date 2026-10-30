from pipeline.creation import create_task
from pipeline.models import ItemStage, Task, TaskItem
from pipeline.outcomes import done, skipped, waiting
from pipeline.stages import Stage
from pipeline.tests.helpers import make_problem
from pipeline.transitions import (
    apply_outcome,
    begin_stage,
    cancel_task,
    complete_item,
    complete_stage,
    fail_stage,
    park_item,
    pause_for_error,
    pause_task,
    refresh_task_status,
    requeue_item,
    resume_task,
    task_is_done,
)
from userconfig import update_config
from userconfig.tests.helpers import DataDirDBTestCase


class TransitionTestCase(DataDirDBTestCase):
    def setUp(self):
        super().setUp()
        update_config({"output_dirs": ["/vault"]})
        self.task = create_task([make_problem("two-sum", 1)]).task
        self.item = self.task.items.get()
        self.row = self.item.stages.get(stage=Stage.FETCH_PROBLEM)

    def reload(self):
        for obj in (self.task, self.item, self.row):
            obj.refresh_from_db()


class StageTransitionTests(TransitionTestCase):
    def test_begin_stage_starts_the_stage_the_item_and_the_task(self):
        begin_stage(self.item, self.row)
        self.reload()
        self.assertEqual(self.row.status, ItemStage.Status.RUNNING)
        self.assertIsNotNone(self.row.started_at)
        self.assertEqual(
            (self.item.status, self.item.stage),
            (TaskItem.Status.RUNNING, Stage.FETCH_PROBLEM),
        )
        self.assertIsNotNone(self.item.started_at)
        self.assertIsNotNone(self.task.started_at)

    def test_begin_stage_clears_an_earlier_error_and_wait(self):
        self.item.error, self.item.input_kind = "old", TaskItem.InputKind.REVIEW
        self.item.save()
        begin_stage(self.item, self.row)
        self.reload()
        self.assertEqual((self.item.error, self.item.input_kind), ("", ""))

    def test_the_start_times_are_set_only_once(self):
        begin_stage(self.item, self.row)
        self.reload()
        item_started, task_started = self.item.started_at, self.task.started_at
        begin_stage(self.item, self.row)
        self.reload()
        self.assertEqual(
            (self.item.started_at, self.task.started_at), (item_started, task_started)
        )

    def test_complete_stage_done_and_skipped(self):
        complete_stage(self.row, done("fetched"))
        self.reload()
        self.assertEqual(
            (self.row.status, self.row.detail), (ItemStage.Status.DONE, "fetched")
        )
        self.assertIsNotNone(self.row.finished_at)
        complete_stage(self.row, skipped("not needed"))
        self.reload()
        self.assertEqual(self.row.status, ItemStage.Status.SKIPPED)

    def test_park_item_waits_for_you_without_finishing_the_stage(self):
        park_item(self.item, self.row, waiting(TaskItem.InputKind.REVIEW, "approve"))
        self.reload()
        self.assertEqual(self.row.status, ItemStage.Status.WAITING)
        self.assertIsNone(self.row.finished_at)
        self.assertEqual(
            (self.item.status, self.item.input_kind),
            (TaskItem.Status.NEEDS_INPUT, TaskItem.InputKind.REVIEW),
        )

    def test_apply_outcome_sends_each_outcome_to_the_right_place(self):
        apply_outcome(self.item, self.row, done())
        self.reload()
        self.assertEqual(
            (self.row.status, self.item.status),
            (ItemStage.Status.DONE, TaskItem.Status.QUEUED),
        )
        apply_outcome(self.item, self.row, waiting(TaskItem.InputKind.MANUAL_PREFILL))
        self.reload()
        self.assertEqual(
            (self.row.status, self.item.status),
            (ItemStage.Status.WAITING, TaskItem.Status.NEEDS_INPUT),
        )

    def test_fail_stage_fails_the_stage_and_the_item(self):
        fail_stage(self.item, self.row, "no such problem")
        self.reload()
        self.assertEqual(
            (self.row.status, self.row.error),
            (ItemStage.Status.FAILED, "no such problem"),
        )
        self.assertEqual(
            (self.item.status, self.item.error),
            (TaskItem.Status.FAILED, "no such problem"),
        )
        self.assertIsNotNone(self.item.finished_at)

    def test_complete_item(self):
        self.item.input_kind = TaskItem.InputKind.REVIEW
        self.item.save()
        complete_item(self.item)
        self.reload()
        self.assertEqual(
            (self.item.status, self.item.input_kind), (TaskItem.Status.COMPLETED, "")
        )
        self.assertIsNotNone(self.item.finished_at)


class TaskTransitionTests(TransitionTestCase):
    def test_pause_for_error_puts_the_stage_back_and_pauses_the_task(self):
        begin_stage(self.item, self.row)
        pause_for_error(self.item, self.row, "cookies expired")
        self.reload()
        self.assertEqual(self.row.status, ItemStage.Status.PENDING)
        self.assertIsNone(self.row.started_at)
        self.assertEqual(self.item.status, TaskItem.Status.QUEUED)
        self.assertEqual(
            (self.task.status, self.task.pause_reason),
            (Task.Status.PAUSED, "cookies expired"),
        )

    def test_pause_and_resume_by_you(self):
        pause_task(self.task)
        self.reload()
        self.assertEqual(
            (self.task.status, self.task.pause_reason),
            (Task.Status.PAUSED, "Paused by you"),
        )
        resume_task(self.task)
        self.reload()
        self.assertEqual(
            (self.task.status, self.task.pause_reason), (Task.Status.ACTIVE, "")
        )

    def test_only_an_active_task_can_be_paused_and_only_a_paused_one_resumed(self):
        resume_task(self.task)  # already active: nothing happens
        cancel_task(self.task)
        pause_task(self.task)  # cancelled: stays cancelled
        resume_task(self.task)
        self.reload()
        self.assertEqual(self.task.status, Task.Status.CANCELLED)

    def test_cancel(self):
        cancel_task(self.task)
        self.reload()
        self.assertEqual(self.task.status, Task.Status.CANCELLED)
        self.assertIsNotNone(self.task.finished_at)

    def test_a_finished_task_is_not_cancelled(self):
        self.task.status = Task.Status.FINISHED
        self.task.save()
        cancel_task(self.task)
        self.reload()
        self.assertEqual(self.task.status, Task.Status.FINISHED)


class TaskStatusRefreshTests(TransitionTestCase):
    def set_item(self, status):
        self.item.status = status
        self.item.save()

    def test_an_open_item_keeps_the_task_active(self):
        for status in (
            TaskItem.Status.QUEUED,
            TaskItem.Status.RUNNING,
            TaskItem.Status.NEEDS_INPUT,
        ):
            with self.subTest(status=status):
                self.set_item(status)
                refresh_task_status(self.task)
                self.reload()
                self.assertFalse(task_is_done(self.task))
                self.assertEqual(self.task.status, Task.Status.ACTIVE)

    def test_the_task_finishes_when_every_item_is_completed_or_failed(self):
        second = TaskItem.objects.create(
            task=self.task,
            problem=make_problem("other", 2),
            status=TaskItem.Status.FAILED,
        )
        self.set_item(TaskItem.Status.COMPLETED)
        refresh_task_status(self.task)
        self.reload()
        self.assertEqual(self.task.status, Task.Status.FINISHED)
        self.assertIsNotNone(self.task.finished_at)
        self.assertEqual(second.status, TaskItem.Status.FAILED)

    def test_a_paused_task_does_not_finish(self):
        pause_task(self.task)
        self.set_item(TaskItem.Status.COMPLETED)
        refresh_task_status(self.task)
        self.reload()
        self.assertEqual(self.task.status, Task.Status.PAUSED)


class RequeueTests(TransitionTestCase):
    def test_requeue_sets_the_next_stage(self):
        begin_stage(self.item, self.row)
        requeue_item(self.item, Stage.FETCH_IMAGES)
        self.reload()
        self.assertEqual(
            (self.item.status, self.item.stage),
            (TaskItem.Status.QUEUED, Stage.FETCH_IMAGES),
        )

    def test_pausing_for_an_error_does_not_undo_a_cancel(self):
        cancel_task(self.task)
        pause_for_error(self.item, self.row, "cookies expired")
        self.reload()
        self.assertEqual(self.task.status, Task.Status.CANCELLED)
