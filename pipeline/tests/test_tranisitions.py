from pipeline.models import ItemStage, PipelineItem
from pipeline.stages.definitions import Stage
from pipeline.stages.outcomes import done, skipped, waiting
from pipeline.tasks.creation import start_pipeline_items
from pipeline.tasks.transitions import (
    apply_outcome,
    begin_stage,
    cancel_item,
    complete_item,
    complete_stage,
    fail_stage,
    park_item,
    pause_for_error,
    pause_item,
    requeue_item,
    resume_item,
)
from pipeline.tests.helpers import make_problem
from userconfig import update_config
from userconfig.tests.helpers import DataDirDBTestCase


class TransitionTestCase(DataDirDBTestCase):
    def setUp(self):
        super().setUp()
        update_config({"output_dirs": ["/vault"]})
        self.item = start_pipeline_items([make_problem("two-sum", 1)], wake=None).items[0]
        self.row = self.item.stages.get(stage=Stage.FETCH_PROBLEM)

    def reload(self):
        for obj in (self.item, self.row):
            obj.refresh_from_db()


class StageTransitionTests(TransitionTestCase):
    def test_begin_stage_starts_the_stage_and_the_item(self):
        begin_stage(self.item, self.row)
        self.reload()
        self.assertEqual(self.row.status, ItemStage.Status.RUNNING)
        self.assertIsNotNone(self.row.started_at)
        self.assertEqual(
            (self.item.status, self.item.stage),
            (PipelineItem.Status.RUNNING, Stage.FETCH_PROBLEM),
        )
        self.assertIsNotNone(self.item.started_at)

    def test_begin_stage_clears_an_earlier_error_and_wait(self):
        self.item.error, self.item.input_kind = "old", PipelineItem.InputKind.REVIEW
        self.item.save()
        begin_stage(self.item, self.row)
        self.reload()
        self.assertEqual((self.item.error, self.item.input_kind), ("", ""))

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
        park_item(
            self.item, self.row, waiting(PipelineItem.InputKind.REVIEW, "approve")
        )
        self.reload()
        self.assertEqual(self.row.status, ItemStage.Status.WAITING)
        self.assertIsNone(self.row.finished_at)
        self.assertEqual(
            (self.item.status, self.item.input_kind),
            (PipelineItem.Status.NEEDS_INPUT, PipelineItem.InputKind.REVIEW),
        )

    def test_apply_outcome_sends_each_outcome_to_the_right_place(self):
        apply_outcome(self.item, self.row, done())
        self.reload()
        self.assertEqual(
            (self.row.status, self.item.status),
            (ItemStage.Status.DONE, PipelineItem.Status.QUEUED),
        )
        apply_outcome(
            self.item, self.row, waiting(PipelineItem.InputKind.MANUAL_PREFILL)
        )
        self.reload()
        self.assertEqual(
            (self.row.status, self.item.status),
            (ItemStage.Status.WAITING, PipelineItem.Status.NEEDS_INPUT),
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
            (PipelineItem.Status.FAILED, "no such problem"),
        )
        self.assertIsNotNone(self.item.finished_at)

    def test_complete_item(self):
        self.item.input_kind = PipelineItem.InputKind.REVIEW
        self.item.save()
        complete_item(self.item)
        self.reload()
        self.assertEqual(
            (self.item.status, self.item.input_kind),
            (PipelineItem.Status.COMPLETED, ""),
        )
        self.assertIsNotNone(self.item.finished_at)


class ItemControlTransitionTests(TransitionTestCase):
    def test_pause_for_error_puts_the_stage_back_and_pauses_the_item(self):
        begin_stage(self.item, self.row)
        pause_for_error(self.item, self.row, "cookies expired")
        self.reload()
        self.assertEqual(self.row.status, ItemStage.Status.PENDING)
        self.assertIsNone(self.row.started_at)
        self.assertEqual(
            (self.item.status, self.item.error),
            (PipelineItem.Status.PAUSED, "cookies expired"),
        )

    def test_pause_and_resume_by_you(self):
        pause_item(self.item)
        self.reload()
        self.assertEqual(
            (self.item.status, self.item.error),
            (PipelineItem.Status.PAUSED, "Paused by you"),
        )
        resume_item(self.item)
        self.reload()
        self.assertEqual(
            (self.item.status, self.item.error),
            (PipelineItem.Status.QUEUED, ""),
        )

    def test_cancel(self):
        cancel_item(self.item)
        self.reload()
        self.assertEqual(self.item.status, PipelineItem.Status.CANCELLED)
        self.assertIsNotNone(self.item.finished_at)


class RequeueTests(TransitionTestCase):
    def test_requeue_sets_the_next_stage(self):
        begin_stage(self.item, self.row)
        requeue_item(self.item, Stage.FETCH_IMAGES)
        self.reload()
        self.assertEqual(
            (self.item.status, self.item.stage),
            (PipelineItem.Status.QUEUED, Stage.FETCH_IMAGES),
        )

    def test_pausing_for_an_error_does_not_undo_a_cancel(self):
        cancel_item(self.item)
        pause_for_error(self.item, self.row, "cookies expired")
        self.reload()
        self.assertEqual(self.item.status, PipelineItem.Status.CANCELLED)
