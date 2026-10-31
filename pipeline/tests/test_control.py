from pipeline.control import cancel, pause, resume, start_task
from pipeline.creation import NothingToDo
from pipeline.engine import run_until_idle
from pipeline.models import Task
from pipeline.tests.helpers import make_problem
from pipeline.tests.test_engine import EngineTestCase


class FakeWake:
    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return True


class ControlTests(EngineTestCase):
    def setUp(self):
        super().setUp()
        self.wake = FakeWake()
        self.a = self.problem("a", 1)

    def status(self, task) -> str:
        return Task.objects.get(pk=task.pk).status

    def test_start_task_creates_the_task_and_wakes_the_worker(self):
        creation = start_task([self.a], wake=self.wake)
        self.assertEqual(creation.task.items.count(), 1)
        self.assertEqual(self.wake.calls, 1)

    def test_a_task_that_cannot_be_created_does_not_wake_the_worker(self):
        unsolved = make_problem("u", 9, solved=False)
        with self.assertRaises(NothingToDo):
            start_task([unsolved], wake=self.wake)
        self.assertEqual(self.wake.calls, 0)

    def test_pause_stops_the_engine_from_picking_up_the_task(self):
        task = self.task_for(self.a)
        pause(task.pk)
        self.assertEqual(run_until_idle(self.deps()), [])
        self.assertEqual(self.status(task), Task.Status.PAUSED)

    def test_resume_continues_the_task_and_wakes_the_worker(self):
        task = self.task_for(self.a, review=False)
        pause(task.pk)
        resume(task.pk, wake=self.wake)
        self.assertEqual(self.wake.calls, 1)
        run_until_idle(self.deps())
        self.assertEqual(self.status(task), Task.Status.FINISHED)

    def test_cancel_stops_the_task_and_frees_its_problems(self):
        task = self.task_for(self.a)
        cancel(task.pk)
        self.assertEqual(run_until_idle(self.deps()), [])
        self.assertEqual(self.status(task), Task.Status.CANCELLED)
        self.task_for(self.a)  # no longer "already in a running task"

    def test_an_unknown_task(self):
        with self.assertRaises(Task.DoesNotExist):
            pause(999)
