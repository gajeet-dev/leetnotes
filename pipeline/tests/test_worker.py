import tempfile
import threading
from pathlib import Path
from unittest import mock

from pipeline.creation import create_task
from pipeline.engine import has_queued_work, next_pending_stage
from pipeline.locking import FileLock
from pipeline.models import ItemStage, Task, TaskItem
from pipeline.pacing import Pacer
from pipeline.tests.helpers import make_problem
from pipeline.tests.test_engine import EngineTestCase
from pipeline.transitions import begin_stage, pause_task
from pipeline.worker import MAX_CRASHES, Worker, WorkerManager
from userconfig import update_config
from userconfig.tests.helpers import DataDirDBTestCase

Item = TaskItem.Status


class WorkerTests(EngineTestCase):
    def setUp(self):
        super().setUp()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.lock_path = Path(folder.name) / "worker.lock"

    def make_worker(self, **options) -> Worker:
        options.setdefault("idle_timeout", 0)  # exit as soon as nothing is left
        options.setdefault("poll_seconds", 0)
        worker = Worker(deps=self.deps(), lock=FileLock(self.lock_path), **options)
        worker.pacer = Pacer(lambda: None, wait=worker.wait)  # no pacing in these tests
        return worker

    def test_runs_everything_then_exits_when_idle(self):
        problem = self.problem("a", 1)
        task = self.task_for(problem, review=False)
        self.assertTrue(self.make_worker().run())
        task.refresh_from_db()
        self.assertEqual(self.item_of(problem).status, Item.COMPLETED)
        self.assertEqual(task.status, Task.Status.FINISHED)

    def test_the_lock_is_released_afterwards(self):
        self.make_worker().run()
        lock = FileLock(self.lock_path)
        self.assertTrue(lock.acquire())
        lock.release()

    def test_does_nothing_while_another_worker_holds_the_lock(self):
        problem = self.problem("a", 1)
        self.task_for(problem)
        other = FileLock(self.lock_path)
        other.acquire()
        self.addCleanup(other.release)
        self.assertFalse(self.make_worker().run())
        self.assertEqual(self.item_of(problem).status, Item.QUEUED)
        self.assertEqual(self.fetcher.calls, [])

    def test_a_worker_told_to_stop_runs_nothing(self):
        problem = self.problem("a", 1)
        self.task_for(problem)
        worker = self.make_worker()
        worker.stop()
        self.assertTrue(worker.run())
        self.assertEqual(self.item_of(problem).status, Item.QUEUED)

    def test_a_paused_task_is_left_alone(self):
        problem = self.problem("a", 1)
        pause_task(self.task_for(problem))
        self.make_worker().run()
        self.assertEqual(self.fetcher.calls, [])
        self.assertEqual(self.item_of(problem).status, Item.QUEUED)

    def test_an_interrupted_stage_is_recovered_at_the_start_and_runs_again(self):
        problem = self.problem("a", 1)
        task = self.task_for(problem, review=False)
        item = self.item_of(problem)
        begin_stage(item, next_pending_stage(item))  # as if the app died here
        self.make_worker().run()
        task.refresh_from_db()
        self.assertEqual(
            (self.item_of(problem).status, task.status),
            (Item.COMPLETED, Task.Status.FINISHED),
        )
        self.assertEqual(self.fetcher.calls.count(("a", "content")), 1)

    def test_the_pacer_is_asked_before_each_stage(self):
        self.task_for(self.problem("a", 1), review=False)
        worker = self.make_worker()
        recorder = mock.Mock()
        recorder.before_stage.return_value = True
        worker.pacer = recorder
        worker.run()
        self.assertEqual(recorder.before_stage.call_count, 5)

    def test_a_stop_while_waiting_for_the_next_turn_leaves_the_rest_queued(self):
        first, second = self.problem("a", 1), self.problem("b", 2)
        self.task_for(first, second, review=False)
        worker = self.make_worker()
        worker.pacer = Pacer(lambda: 10.0, wait=lambda seconds: worker.stop() or False)
        worker.run()
        self.assertEqual(self.item_of(first).status, Item.COMPLETED)
        self.assertEqual(self.item_of(second).status, Item.QUEUED)
        self.assertEqual({slug for slug, _ in self.fetcher.calls}, {"a"})

    def test_repeated_crashes_stop_the_worker_instead_of_spinning(self):
        problem = self.problem("a", 1)
        self.task_for(problem)
        worker = self.make_worker(idle_timeout=None)
        with mock.patch(
            "pipeline.worker.step", side_effect=RuntimeError("db gone")
        ) as fake_step:
            self.assertTrue(worker.run())
        self.assertEqual(fake_step.call_count, MAX_CRASHES)
        self.assertEqual(self.item_of(problem).status, Item.QUEUED)


class FakeWorker:
    """Stands in for a Worker so the manager can be tested without threads."""

    def __init__(self, owned: bool = True, on_run=None):
        self.owned, self.on_run, self.runs = owned, on_run, 0
        self.stop_event = threading.Event()

    def run(self) -> bool:
        self.runs += 1
        if self.on_run:
            self.on_run(self.runs)
        return self.owned

    def stop(self) -> None:
        self.stop_event.set()


class WorkerManagerTests(DataDirDBTestCase):
    def setUp(self):
        super().setUp()
        update_config({"output_dirs": ["/vault"]})
        self.spawned = []

    def manager(self, worker: FakeWorker) -> WorkerManager:
        return WorkerManager(spawn=self.spawned.append, make_worker=lambda: worker)

    def test_only_one_worker_is_started(self):
        manager = self.manager(FakeWorker())
        self.assertTrue(manager.ensure_running())
        self.assertFalse(manager.ensure_running())
        self.assertEqual(len(self.spawned), 1)
        self.assertTrue(manager.is_running)

    def test_it_is_cleared_when_there_is_nothing_left_so_a_new_one_can_start(self):
        manager = self.manager(FakeWorker())
        manager.ensure_running()
        self.spawned[0]()
        self.assertFalse(manager.is_running)
        self.assertTrue(manager.ensure_running())

    def test_work_that_arrives_just_as_the_worker_finishes_is_not_missed(self):
        problem = make_problem("a", 1)

        def on_run(run_number):
            if run_number == 1:
                create_task([problem])  # new work appears as the first run ends
            else:
                TaskItem.objects.update(status=Item.COMPLETED)

        worker = FakeWorker(on_run=on_run)
        manager = self.manager(worker)
        manager.ensure_running()
        self.spawned[0]()
        self.assertEqual(worker.runs, 2)
        self.assertFalse(manager.is_running)

    def test_a_worker_that_lost_the_lock_does_not_retry(self):
        create_task([make_problem("a", 1)])
        worker = FakeWorker(owned=False)
        manager = self.manager(worker)
        manager.ensure_running()
        self.spawned[0]()
        self.assertEqual(worker.runs, 1)
        self.assertFalse(manager.is_running)

    def test_a_worker_that_was_stopped_is_not_restarted_even_with_work_left(self):
        create_task([make_problem("a", 1)])
        worker = FakeWorker(on_run=lambda number: worker.stop())
        manager = self.manager(worker)
        manager.ensure_running()
        self.spawned[0]()
        self.assertEqual(worker.runs, 1)
        self.assertTrue(has_queued_work())

    def test_a_crash_clears_the_manager_so_it_can_start_again(self):
        def boom(number):
            raise RuntimeError("boom")

        manager = self.manager(FakeWorker(on_run=boom))
        manager.ensure_running()
        with self.assertRaises(RuntimeError):
            self.spawned[0]()
        self.assertFalse(manager.is_running)

    def test_stop_reaches_the_running_worker(self):
        worker = FakeWorker()
        manager = self.manager(worker)
        manager.stop()  # nothing running: fine
        manager.ensure_running()
        manager.stop()
        self.assertTrue(worker.stop_event.is_set())
