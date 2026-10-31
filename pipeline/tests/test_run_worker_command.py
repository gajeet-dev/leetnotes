import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError

from pipeline.locking import FileLock
from pipeline.pacing import Pacer
from pipeline.tests.test_engine import EngineTestCase
from pipeline.worker import Worker

TARGET = "pipeline.management.commands.run_worker"


class RunWorkerCommandTests(EngineTestCase):
    def setUp(self):
        super().setUp()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.lock_path = Path(folder.name) / "worker.lock"
        patcher = mock.patch(
            f"{TARGET}.signal"
        )  # don't change the test runner's signal handling
        self.signal = patcher.start()
        self.addCleanup(patcher.stop)

    def real_worker(self) -> Worker:
        worker = Worker(
            idle_timeout=0,
            poll_seconds=0,
            deps=self.deps(),
            lock=FileLock(self.lock_path),
        )
        worker.pacer = Pacer(lambda: None, wait=worker.wait)  # no pacing in tests
        return worker

    def run_command(self, worker, *args):
        out = StringIO()
        with mock.patch(f"{TARGET}.make_worker", return_value=worker) as make:
            call_command("run_worker", *args, stdout=out)
        return out.getvalue(), make

    def fake_worker(self) -> mock.Mock:
        worker = mock.Mock()
        worker.run.return_value = True
        return worker

    def test_runs_a_task_to_the_end_and_prints_the_summary(self):
        self.task_for(self.problem("a", 1), review=False)
        output, _ = self.run_command(self.real_worker())
        self.assertIn("[finished]: 1 completed", output)

    def test_no_tasks(self):
        output, _ = self.run_command(self.real_worker())
        self.assertIn("No tasks.", output)

    def test_the_idle_timeout_options(self):
        _, make = self.run_command(self.fake_worker())
        make.assert_called_once_with(0.0)
        _, make = self.run_command(self.fake_worker(), "--idle-timeout", "5")
        make.assert_called_once_with(5.0)
        _, make = self.run_command(self.fake_worker(), "--forever")
        make.assert_called_once_with(None)

    def test_it_refuses_when_another_worker_holds_the_lock(self):
        other = FileLock(self.lock_path)
        other.acquire()
        self.addCleanup(other.release)
        with self.assertRaisesMessage(CommandError, "already running"):
            self.run_command(self.real_worker())

    def test_the_first_ctrl_c_stops_gently_and_the_second_quits_now(self):
        worker = self.fake_worker()
        self.run_command(worker)
        calls = self.signal.signal.call_args_list
        self.assertEqual(len(calls), 2)  # SIGINT and SIGTERM
        handler = calls[0].args[1]
        handler(2, None)
        worker.stop.assert_called_once()
        with self.assertRaises(KeyboardInterrupt):
            handler(2, None)

    def test_a_forced_quit_is_reported_and_does_not_raise(self):
        worker = self.fake_worker()
        worker.run.side_effect = KeyboardInterrupt
        output, _ = self.run_command(worker)
        self.assertIn("resumes next time", output)
