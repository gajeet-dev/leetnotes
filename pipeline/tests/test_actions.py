import json

from leetcode.exceptions import NotFoundError
from pipeline.actions import (
    ActionError,
    approve_all_reviews,
    approve_review,
    reject_review,
    retry_failed_item,
    submit_manual_reply,
)
from pipeline.models import ItemStage, Task, TaskItem
from pipeline.stages import Stage
from pipeline.tests.test_engine import EngineTestCase
from pipeline.transitions import cancel_task
from prefill.models import AIResponse
from prefill.tests.test_parsing import valid
from problems.models import SyncTask

Item = TaskItem.Status


class FakeWake:
    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return True


class ApproveReviewTests(EngineTestCase):
    def setUp(self):
        super().setUp()
        self.wake = FakeWake()
        self.a = self.problem("a", 1)
        self.task = self.task_for(self.a)  # review is on
        self.run_all()  # runs up to the review and waits there
        self.item = self.item_of(self.a)

    def reload(self):
        self.item.refresh_from_db()
        self.task.refresh_from_db()

    def test_the_item_is_waiting_for_review_to_begin_with(self):
        self.assertEqual(self.item.status, Item.NEEDS_INPUT)

    def test_approving_queues_the_item_for_its_next_stage_and_wakes_the_worker(self):
        approve_review(self.item.pk, wake=self.wake)
        self.reload()
        review = self.item.stages.get(stage=Stage.REVIEW)
        self.assertEqual(
            (review.status, review.detail), (ItemStage.Status.DONE, "approved")
        )
        self.assertEqual(
            (self.item.status, self.item.stage, self.item.input_kind),
            (Item.QUEUED, Stage.SAVE_NOTES, ""),
        )
        self.assertEqual(self.wake.calls, 1)

    def test_the_approved_item_then_runs_to_the_end(self):
        approve_review(self.item.pk, wake=self.wake)
        self.run_all()
        self.reload()
        self.assertEqual(
            (self.item.status, self.task.status), (Item.COMPLETED, Task.Status.FINISHED)
        )

    def test_an_item_that_is_not_waiting_cannot_be_approved(self):
        approve_review(self.item.pk, wake=self.wake)
        with self.assertRaisesMessage(ActionError, "not waiting for review"):
            approve_review(self.item.pk, wake=self.wake)  # it is queued now

    def test_an_item_waiting_for_something_else_cannot_be_approved(self):
        TaskItem.objects.filter(pk=self.item.pk).update(
            input_kind=TaskItem.InputKind.MANUAL_PREFILL
        )
        with self.assertRaises(ActionError):
            approve_review(self.item.pk, wake=self.wake)

    def test_a_cancelled_task_cannot_be_approved(self):
        cancel_task(self.task)
        with self.assertRaisesMessage(ActionError, "cancelled"):
            approve_review(self.item.pk, wake=self.wake)
        self.assertEqual(self.wake.calls, 0)


class ApproveAllTests(EngineTestCase):
    def setUp(self):
        super().setUp()
        self.wake = FakeWake()
        self.a, self.b = self.problem("a", 1), self.problem("b", 2)
        self.task = self.task_for(self.a, self.b)
        self.run_all()

    def test_every_waiting_item_is_approved_and_the_worker_woken_once(self):
        self.assertEqual(approve_all_reviews(self.task.pk, wake=self.wake), 2)
        self.assertEqual(self.wake.calls, 1)
        self.run_all()
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.FINISHED)
        for problem in (self.a, self.b):
            self.assertEqual(self.item_of(problem).status, Item.COMPLETED)

    def test_nothing_waiting_means_nothing_done_and_no_wake(self):
        approve_all_reviews(self.task.pk, wake=self.wake)
        self.assertEqual(approve_all_reviews(self.task.pk, wake=self.wake), 0)
        self.assertEqual(self.wake.calls, 1)

    def test_items_waiting_for_something_else_are_left_alone(self):
        TaskItem.objects.filter(problem=self.b).update(
            input_kind=TaskItem.InputKind.MANUAL_PREFILL
        )
        self.assertEqual(approve_all_reviews(self.task.pk, wake=self.wake), 1)
        self.assertEqual(self.item_of(self.b).status, Item.NEEDS_INPUT)


class SubmitManualReplyTests(EngineTestCase):
    def setUp(self):
        super().setUp()
        self.wake = FakeWake()
        self.a = self.problem("a", 1)
        self.task = self.task_for(self.a, provider="manual", review=False)
        self.run_all()  # fetches, then waits for a pasted reply
        self.item = self.item_of(self.a)

    def reload(self):
        self.item.refresh_from_db()

    def submit(self, text, **options):
        return submit_manual_reply(self.item.pk, text, wake=self.wake, **options)

    def test_the_item_is_waiting_for_a_reply_to_begin_with(self):
        self.assertEqual(
            (self.item.status, self.item.input_kind),
            (Item.NEEDS_INPUT, TaskItem.InputKind.MANUAL_PREFILL),
        )

    def test_a_valid_reply_becomes_the_active_note_and_the_item_moves_on(self):
        self.submit(json.dumps(valid()), label="claude.ai")
        self.reload()
        response = AIResponse.objects.get(problem=self.a)
        self.assertTrue(response.is_active)
        self.assertEqual(
            (response.source, response.provider_name), ("manual", "claude.ai")
        )
        prefill = self.item.stages.get(stage=Stage.GENERATE_PREFILL)
        self.assertEqual(prefill.status, ItemStage.Status.DONE)
        self.assertEqual(
            (self.item.status, self.item.stage, self.item.input_kind),
            (Item.QUEUED, Stage.SAVE_NOTES, ""),
        )
        self.assertEqual(self.wake.calls, 1)

    def test_the_item_then_runs_to_the_end(self):
        self.submit(json.dumps(valid()))
        self.run_all()
        self.reload()
        self.assertEqual(self.item.status, Item.COMPLETED)

    def test_an_invalid_reply_is_rejected_and_the_item_keeps_waiting(self):
        with self.assertRaisesMessage(ActionError, "isn't valid"):
            self.submit("this is not json")
        self.reload()
        self.assertEqual(self.item.status, Item.NEEDS_INPUT)
        self.assertEqual(AIResponse.objects.count(), 0)
        self.assertEqual(self.wake.calls, 0)

    def test_an_item_that_is_not_waiting_for_a_reply_is_refused(self):
        TaskItem.objects.filter(pk=self.item.pk).update(
            input_kind=TaskItem.InputKind.REVIEW
        )
        with self.assertRaisesMessage(ActionError, "not waiting for manual prefill"):
            self.submit(json.dumps(valid()))

    def test_a_cancelled_task_is_refused(self):
        cancel_task(self.task)
        with self.assertRaisesMessage(ActionError, "cancelled"):
            self.submit(json.dumps(valid()))


class RejectReviewTests(EngineTestCase):
    def setUp(self):
        super().setUp()
        self.wake = FakeWake()
        self.a = self.problem("a", 1)
        self.task = self.task_for(self.a)  # review is on
        self.run_all()
        self.item = self.item_of(self.a)

    def test_the_item_goes_back_to_generating_and_the_worker_is_woken(self):
        reject_review(self.item.pk, wake=self.wake)
        self.item.refresh_from_db()
        statuses = self.stage_statuses(self.item)
        self.assertEqual(statuses[Stage.GENERATE_PREFILL], ItemStage.Status.PENDING)
        self.assertEqual(statuses[Stage.REVIEW], ItemStage.Status.PENDING)
        self.assertEqual(
            (self.item.status, self.item.stage, self.item.input_kind),
            (Item.QUEUED, Stage.GENERATE_PREFILL, ""),
        )
        self.assertEqual(self.wake.calls, 1)

    def test_a_new_note_is_generated_and_waits_for_review_again(self):
        reject_review(self.item.pk, wake=self.wake)
        self.run_all()
        self.item.refresh_from_db()
        self.assertEqual(
            (self.item.status, self.item.input_kind),
            (Item.NEEDS_INPUT, TaskItem.InputKind.REVIEW),
        )
        self.assertEqual(AIResponse.objects.filter(problem=self.a).count(), 2)
        self.assertEqual(
            AIResponse.objects.filter(problem=self.a, is_active=True).count(), 1
        )

    def test_an_item_that_is_not_waiting_for_review_is_refused(self):
        reject_review(self.item.pk, wake=self.wake)
        with self.assertRaisesMessage(ActionError, "not waiting for review"):
            reject_review(self.item.pk, wake=self.wake)

    def test_a_cancelled_task_is_refused(self):
        cancel_task(self.task)
        with self.assertRaisesMessage(ActionError, "cancelled"):
            reject_review(self.item.pk, wake=self.wake)


class RetryFailedItemTests(EngineTestCase):
    def setUp(self):
        super().setUp()
        self.wake = FakeWake()
        self.a = self.problem("a", 1)
        self.fetcher.fail[("a", SyncTask.Part.CONTENT)] = NotFoundError("gone")
        self.task = self.task_for(self.a, review=False)
        self.run_all()
        self.item = self.item_of(self.a)

    def test_the_item_and_task_start_out_failed_and_finished(self):
        self.task.refresh_from_db()
        self.assertEqual(self.item.status, Item.FAILED)
        self.assertEqual(self.task.status, Task.Status.FINISHED)

    def test_retrying_resets_the_failed_stage_and_reopens_the_task(self):
        retry_failed_item(self.item.pk, wake=self.wake)
        self.item.refresh_from_db()
        self.task.refresh_from_db()
        row = self.item.stages.get(stage=Stage.FETCH_PROBLEM)
        self.assertEqual((row.status, row.error), (ItemStage.Status.PENDING, ""))
        self.assertEqual(
            (self.item.status, self.item.stage, self.item.error),
            (Item.QUEUED, Stage.FETCH_PROBLEM, ""),
        )
        self.assertEqual(self.task.status, Task.Status.ACTIVE)
        self.assertIsNone(self.task.finished_at)
        self.assertEqual(self.wake.calls, 1)

    def test_the_retried_item_runs_to_the_end_once_the_cause_is_fixed(self):
        self.fetcher.fail.clear()
        retry_failed_item(self.item.pk, wake=self.wake)
        self.run_all()
        self.item.refresh_from_db()
        self.task.refresh_from_db()
        self.assertEqual(
            (self.item.status, self.task.status), (Item.COMPLETED, Task.Status.FINISHED)
        )

    def test_it_fails_again_if_the_cause_is_still_there(self):
        retry_failed_item(self.item.pk, wake=self.wake)
        self.run_all()
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, Item.FAILED)

    def test_an_item_that_has_not_failed_is_refused(self):
        retry_failed_item(self.item.pk, wake=self.wake)
        with self.assertRaisesMessage(ActionError, "has not failed"):
            retry_failed_item(self.item.pk, wake=self.wake)

    def test_a_cancelled_task_is_refused(self):
        Task.objects.filter(pk=self.task.pk).update(status=Task.Status.CANCELLED)
        with self.assertRaisesMessage(ActionError, "cancelled"):
            retry_failed_item(self.item.pk, wake=self.wake)
