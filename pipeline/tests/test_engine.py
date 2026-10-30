import json
import tempfile
from pathlib import Path
from unittest import mock

from django.utils import timezone

from leetcode.exceptions import AuthError, NotFoundError, TransportError
from notes.models import NoteRecord
from notes.state import expected_path
from pipeline.creation import create_task
from pipeline.engine import next_pending_stage, next_queued_item, run_until_idle, step
from pipeline.handlers import StageDeps
from pipeline.models import ItemStage, Task, TaskItem
from pipeline.stages import Stage
from pipeline.tests.helpers import make_problem, mark_fetched
from pipeline.tests.test_note_handlers import FakeProvider, add_submission
from pipeline.transitions import cancel_task, pause_task, resume_task
from prefill.models import AIResponse
from prefill.providers.base import ProviderResult
from prefill.services import save_response
from prefill.tests.test_parsing import valid
from problems.models import Problem, Submission, SyncTask
from userconfig import update_config
from userconfig.tests.helpers import DataDirDBTestCase

Row = ItemStage.Status
Item = TaskItem.Status


class FakeFetcher:
    """Stands in for run_part: stores what a real fetch would, or raises what you tell it to."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.fail: dict[tuple[str, str], Exception] = {}
        self.submitted_at = timezone.now()

    def __call__(self, problem, part, client, downloader):
        self.calls.append((problem.slug, part))
        if error := self.fail.get((problem.slug, part)):
            raise error
        SyncTask.objects.update_or_create(
            problem=problem, part=part, defaults={"status": SyncTask.Status.DONE}
        )
        if part == SyncTask.Part.CONTENT:
            Problem.objects.filter(pk=problem.pk).update(
                raw_question_html="<p>x</p>",
                content_local_markdown="Statement.",
                difficulty="Easy",
            )
        if part == SyncTask.Part.SUBMISSIONS:
            Submission.objects.get_or_create(
                problem=problem,
                submission_date=self.submitted_at,
                defaults={"lang": "python3", "code": "pass"},
            )


class PickyProvider:
    """Gives a broken reply for two-sum and a valid one for everything else."""

    name, model = "picky", "picky-1"

    def generate(self, system, user):
        text = "not json" if "two-sum" in user else json.dumps(valid())
        return ProviderResult(text, "picky-1")


class EngineTestCase(DataDirDBTestCase):
    def setUp(self):
        super().setUp()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.output = Path(folder.name)
        update_config({"output_dirs": [str(self.output)]})
        self.fetcher = FakeFetcher()
        patcher = mock.patch("pipeline.handlers.run_part", self.fetcher)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.provider = lambda name: FakeProvider(json.dumps(valid()))

    def deps(self) -> StageDeps:
        return StageDeps(
            get_client=lambda: "client",
            get_downloader=lambda: "downloader",
            get_provider=self.provider,
        )

    def problem(self, slug: str, leetcode_id: int) -> Problem:
        return make_problem(
            slug,
            leetcode_id,
            url=f"https://leetcode.com/problems/{slug}/",
            tags=[],
            difficulty="Easy",
            content_local_markdown="Statement.",
        )

    def task_for(self, *problems, **overrides) -> Task:
        overrides.setdefault(
            "provider", "claude"
        )  # any known name: the fake provider is used
        return create_task(list(problems), overrides).task

    def run_all(self, **options):
        return run_until_idle(self.deps(), **options)

    def item_of(self, problem: Problem) -> TaskItem:
        return TaskItem.objects.get(problem=problem)

    def stage_statuses(self, item: TaskItem) -> dict[str, str]:
        return {row.stage: row.status for row in item.stages.all()}


class FullRunTests(EngineTestCase):
    def test_a_task_runs_to_the_end_and_writes_both_notes(self):
        two_sum = self.problem("two-sum", 1)
        task = self.task_for(two_sum, review=False)
        results = self.run_all()
        task.refresh_from_db()
        item = self.item_of(two_sum)
        self.assertEqual(task.status, Task.Status.FINISHED)
        self.assertEqual(item.status, Item.COMPLETED)
        self.assertEqual(
            len(results), 5
        )  # three fetches, prefill, save (review starts skipped)
        statuses = self.stage_statuses(item)
        self.assertEqual(statuses[Stage.REVIEW], Row.SKIPPED)
        self.assertTrue(
            all(status in (Row.DONE, Row.SKIPPED) for status in statuses.values())
        )
        self.assertTrue(
            expected_path(two_sum, NoteRecord.Kind.SOLUTION, self.output).exists()
        )
        self.assertTrue(
            expected_path(two_sum, NoteRecord.Kind.PROBLEM, self.output).exists()
        )
        self.assertEqual(
            AIResponse.objects.filter(problem=two_sum, is_active=True).count(), 1
        )

    def test_stages_already_done_are_not_run_again(self):
        two_sum = self.problem("two-sum", 1)
        mark_fetched(two_sum)
        add_submission(two_sum)
        save_response(two_sum, json.dumps(valid()), source=AIResponse.Source.MANUAL)
        self.task_for(two_sum, review=False)
        results = self.run_all()
        self.assertEqual(self.fetcher.calls, [])
        self.assertEqual([result.stage for result in results], [Stage.SAVE_NOTES])
        self.assertEqual(self.item_of(two_sum).status, Item.COMPLETED)

    def test_items_run_in_order_one_at_a_time(self):
        first, second = (
            self.problem("two-sum", 1),
            self.problem("contains-duplicate", 217),
        )
        self.task_for(first, second, review=False)
        results = self.run_all()
        self.assertEqual(
            [result.item.problem.slug for result in results],
            ["two-sum"] * 5 + ["contains-duplicate"] * 5,
        )


class WaitingTests(EngineTestCase):
    def test_review_waits_for_you_and_keeps_the_task_open(self):
        two_sum = self.problem("two-sum", 1)
        task = self.task_for(two_sum)  # review is on by default
        self.run_all()
        task.refresh_from_db()
        item = self.item_of(two_sum)
        self.assertEqual(
            (item.status, item.input_kind, item.stage),
            (Item.NEEDS_INPUT, TaskItem.InputKind.REVIEW, Stage.REVIEW),
        )
        statuses = self.stage_statuses(item)
        self.assertEqual(
            (statuses[Stage.REVIEW], statuses[Stage.SAVE_NOTES]),
            (Row.WAITING, Row.PENDING),
        )
        self.assertEqual(task.status, Task.Status.ACTIVE)

    def test_the_manual_provider_waits_for_a_pasted_reply(self):
        two_sum = self.problem("two-sum", 1)
        self.task_for(two_sum, provider="manual")
        self.run_all()
        item = self.item_of(two_sum)
        self.assertEqual(
            (item.status, item.input_kind, item.stage),
            (
                Item.NEEDS_INPUT,
                TaskItem.InputKind.MANUAL_PREFILL,
                Stage.GENERATE_PREFILL,
            ),
        )

    def test_a_waiting_item_does_not_block_the_others(self):
        waiting_problem, other = (
            self.problem("two-sum", 1),
            self.problem("contains-duplicate", 217),
        )
        self.provider = lambda name: PickyProvider()
        task = self.task_for(waiting_problem, other, review=False)
        self.run_all()
        task.refresh_from_db()
        self.assertEqual(self.item_of(waiting_problem).status, Item.NEEDS_INPUT)
        self.assertEqual(self.item_of(other).status, Item.COMPLETED)
        self.assertEqual(task.status, Task.Status.ACTIVE)


class FailureTests(EngineTestCase):
    def test_a_failed_stage_fails_only_that_item(self):
        gone, fine = self.problem("gone", 1), self.problem("fine", 2)
        self.fetcher.fail[("gone", SyncTask.Part.CONTENT)] = NotFoundError(
            "no such problem"
        )
        task = self.task_for(gone, fine, review=False)
        self.run_all()
        task.refresh_from_db()
        failed = self.item_of(gone)
        self.assertEqual(
            (failed.status, failed.error), (Item.FAILED, "no such problem")
        )
        row = failed.stages.get(stage=Stage.FETCH_PROBLEM)
        self.assertEqual((row.status, row.error), (Row.FAILED, "no such problem"))
        self.assertEqual(self.item_of(fine).status, Item.COMPLETED)
        self.assertEqual(task.status, Task.Status.FINISHED)

    def test_a_network_error_fails_the_item_and_does_not_pause_the_task(self):
        problem = self.problem("two-sum", 1)
        self.fetcher.fail[("two-sum", SyncTask.Part.CONTENT)] = TransportError(
            "timed out"
        )
        task = self.task_for(problem)
        self.run_all()
        task.refresh_from_db()
        self.assertEqual(self.item_of(problem).status, Item.FAILED)
        self.assertNotEqual(task.status, Task.Status.PAUSED)

    def test_a_bug_in_a_stage_fails_the_item_without_stopping_the_engine(self):
        broken, fine = self.problem("broken", 1), self.problem("fine", 2)
        self.fetcher.fail[("broken", SyncTask.Part.CONTENT)] = RuntimeError("boom")
        self.task_for(broken, fine, review=False)
        self.run_all()
        item = self.item_of(broken)
        self.assertEqual(item.status, Item.FAILED)
        self.assertIn("Unexpected error: RuntimeError: boom", item.error)
        self.assertEqual(self.item_of(fine).status, Item.COMPLETED)


class PauseTests(EngineTestCase):
    def test_expired_cookies_pause_the_whole_task_and_resuming_continues(self):
        problem = self.problem("two-sum", 1)
        self.fetcher.fail[("two-sum", SyncTask.Part.CONTENT)] = AuthError("HTTP 403")
        task = self.task_for(problem, review=False)

        results = self.run_all()
        task.refresh_from_db()
        item = self.item_of(problem)
        self.assertEqual(len(results), 1)
        self.assertEqual(task.status, Task.Status.PAUSED)
        self.assertIn("expired", task.pause_reason)
        self.assertEqual(item.status, Item.QUEUED)  # not failed: it never really ran
        self.assertEqual(item.stages.get(stage=Stage.FETCH_PROBLEM).status, Row.PENDING)

        self.fetcher.fail.clear()
        resume_task(task)
        self.run_all()
        task.refresh_from_db()
        self.assertEqual(self.item_of(problem).status, Item.COMPLETED)
        self.assertEqual(task.status, Task.Status.FINISHED)

    def test_a_task_you_paused_runs_nothing_until_resumed(self):
        task = self.task_for(self.problem("two-sum", 1))
        pause_task(task)
        self.assertEqual(self.run_all(), [])
        resume_task(task)
        self.assertNotEqual(self.run_all(), [])

    def test_a_cancelled_task_runs_nothing(self):
        task = self.task_for(self.problem("two-sum", 1))
        cancel_task(task)
        self.assertEqual(self.run_all(), [])


class ChoosingTests(EngineTestCase):
    def test_nothing_queued(self):
        self.assertIsNone(next_queued_item())
        self.assertIsNone(step(self.deps()))

    def test_oldest_task_first_then_position_and_only_active_tasks(self):
        a, b, c = (self.problem("a", 1), self.problem("b", 2), self.problem("c", 3))
        first, second = self.task_for(a, b), self.task_for(c)
        self.assertEqual(next_queued_item().problem.slug, "a")
        self.assertEqual(next_queued_item(second).problem.slug, "c")
        pause_task(first)
        self.assertEqual(next_queued_item().problem.slug, "c")

    def test_a_waiting_item_is_not_chosen(self):
        problem = self.problem("a", 1)
        self.task_for(problem)
        TaskItem.objects.update(status=Item.NEEDS_INPUT)
        self.assertIsNone(next_queued_item())

    def test_next_pending_stage_is_the_first_one_not_yet_run(self):
        problem = self.problem("a", 1)
        self.task_for(problem)
        item = self.item_of(problem)
        self.assertEqual(next_pending_stage(item).stage, Stage.FETCH_PROBLEM)
        item.stages.filter(stage=Stage.FETCH_PROBLEM).update(status=Row.DONE)
        self.assertEqual(next_pending_stage(item).stage, Stage.FETCH_IMAGES)
        item.stages.update(status=Row.DONE)
        self.assertIsNone(next_pending_stage(item))

    def test_step_reports_what_it_ran(self):
        problem = self.problem("a", 1)
        self.task_for(problem)
        result = step(self.deps())
        self.assertEqual((result.stage, result.status), (Stage.FETCH_PROBLEM, Row.DONE))
        self.assertEqual(result.item.problem.slug, "a")

    def test_the_safety_limit_stops_the_loop(self):
        self.task_for(self.problem("a", 1))
        self.assertEqual(len(self.run_all(max_steps=2)), 2)

    def test_an_item_with_no_stages_left_is_finished(self):
        problem = self.problem("a", 1)
        task = self.task_for(problem)
        self.item_of(problem).stages.update(status=Row.DONE)
        result = step(self.deps())
        task.refresh_from_db()
        self.assertEqual(
            (result.stage, self.item_of(problem).status), (None, Item.COMPLETED)
        )
        self.assertEqual(task.status, Task.Status.FINISHED)


class BeforeStageHookTests(EngineTestCase):
    def test_the_hook_sees_each_stage_before_it_runs(self):
        self.task_for(self.problem("a", 1), review=False)
        seen = []

        def hook(item, row):
            seen.append((row.stage, row.status))
            return True

        run_until_idle(self.deps(), before_stage=hook)
        self.assertEqual(seen[0], (Stage.FETCH_PROBLEM, Row.PENDING))  # not started yet
        self.assertEqual(
            [stage for stage, _ in seen],
            [
                Stage.FETCH_PROBLEM,
                Stage.FETCH_IMAGES,
                Stage.FETCH_SUBMISSION,
                Stage.GENERATE_PREFILL,
                Stage.SAVE_NOTES,
            ],
        )

    def test_returning_false_runs_nothing(self):
        problem = self.problem("a", 1)
        self.task_for(problem)
        self.assertIsNone(step(self.deps(), before_stage=lambda item, row: False))
        self.assertEqual(self.fetcher.calls, [])
        self.assertEqual(self.item_of(problem).status, Item.QUEUED)

    def test_a_task_paused_while_the_hook_waited_is_not_run(self):
        problem = self.problem("a", 1)
        task = self.task_for(problem)

        def hook(item, row):
            pause_task(task)
            return True

        self.assertIsNone(step(self.deps(), before_stage=hook))
        self.assertEqual(self.fetcher.calls, [])
        self.assertEqual(self.item_of(problem).status, Item.QUEUED)
