import tempfile
import threading
from pathlib import Path
from unittest import mock

from pipeline.engine import has_queued_work, next_pending_stage
from pipeline.models import ItemStage, Task, TaskItem
from pipeline.runtime.locking import FileLock
from pipeline.runtime.pacing import Pacer
from pipeline.runtime.worker import (
    MAX_CRASHES,
    Worker,
    ensure_worker_running,
    get_worker,
    is_worker_running,
    reset_worker_state,
    start_worker,
    stop_worker,
    wake_worker,
)
from pipeline.tasks.creation import create_task
from pipeline.tasks.transitions import begin_stage, pause_task
from pipeline.tests.helpers import make_problem
from pipeline.tests.test_engine import EngineTestCase
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
        worker = Worker(deps=self.deps(), lock=FileLock(self.lock_path), **options)
        worker.pacer = Pacer(lambda: None, wait=worker.wait)  # no pacing in these tests
        return worker

    def test_runs_everything_until_queue_is_empty(self):
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
        worker = self.make_worker()
        with mock.patch(
            "pipeline.runtime.worker.step", side_effect=RuntimeError("db gone")
        ) as fake_step:
            self.assertTrue(worker.run())
        self.assertEqual(fake_step.call_count, MAX_CRASHES)
        self.assertEqual(self.item_of(problem).status, Item.QUEUED)

    def test_run_forever_processes_work_when_woken(self):
        problem = self.problem("a", 1)
        task = self.task_for(problem, review=False)
        worker = self.make_worker()

        orig_drain = worker.drain_queue

        def drain_and_stop():
            res = orig_drain()
            worker.stop()
            return res

        worker.drain_queue = drain_and_stop

        self.assertTrue(worker.run_forever())
        task.refresh_from_db()
        self.assertEqual(self.item_of(problem).status, Item.COMPLETED)
        self.assertEqual(task.status, Task.Status.FINISHED)


class WorkerControlTests(DataDirDBTestCase):
    def setUp(self):
        super().setUp()
        reset_worker_state()

    def tearDown(self):
        reset_worker_state()
        super().tearDown()

    def test_start_worker_starts_thread_once(self):
        fake_thread = mock.Mock(is_alive=mock.Mock(return_value=True))
        with mock.patch("pipeline.runtime.worker._spawn_thread", return_value=fake_thread) as mock_spawn:
            self.assertTrue(start_worker())
            self.assertFalse(start_worker())
            self.assertEqual(mock_spawn.call_count, 1)

    def test_wake_worker_signals_the_worker(self):
        worker = get_worker()
        self.assertFalse(worker.work_available.is_set())
        with mock.patch("pipeline.runtime.worker.start_worker"):
            wake_worker()
        self.assertTrue(worker.work_available.is_set())

    def test_ensure_worker_running_wakes_worker(self):
        worker = get_worker()
        self.assertFalse(worker.work_available.is_set())
        with mock.patch("pipeline.runtime.worker.start_worker"):
            ensure_worker_running()
        self.assertTrue(worker.work_available.is_set())

    def test_stop_worker_stops_the_worker(self):
        worker = get_worker()
        self.assertFalse(worker.stop_event.is_set())
        stop_worker()
        self.assertTrue(worker.stop_event.is_set())
