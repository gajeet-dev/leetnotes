from pipeline.engine import run_until_idle
from pipeline.models import PipelineItem
from pipeline.tasks.control import cancel, pause, resume, start_pipeline
from pipeline.tasks.creation import NothingToDo
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

    def status(self, item) -> str:
        return PipelineItem.objects.get(pk=item.pk).status

    def test_start_pipeline_creates_the_items_and_wakes_the_worker(self):
        creation = start_pipeline([self.a], wake=self.wake)
        self.assertEqual(len(creation.items), 1)
        self.assertEqual(self.wake.calls, 1)

    def test_pipeline_that_cannot_be_created_does_not_wake_the_worker(self):
        unsolved = make_problem("u", 9, solved=False)
        with self.assertRaises(NothingToDo):
            start_pipeline([unsolved], wake=self.wake)
        self.assertEqual(self.wake.calls, 0)

    def test_pause_stops_the_engine_from_picking_up_the_item(self):
        item = self.item_for(self.a)
        pause(item.pk)
        self.assertEqual(run_until_idle(self.deps()), [])
        self.assertEqual(self.status(item), PipelineItem.Status.PAUSED)

    def test_resume_continues_the_item_and_wakes_the_worker(self):
        item = self.item_for(self.a, review=False)
        pause(item.pk)
        resume(item.pk, wake=self.wake)
        self.assertEqual(self.wake.calls, 1)
        run_until_idle(self.deps())
        self.assertEqual(self.status(item), PipelineItem.Status.COMPLETED)

    def test_cancel_stops_the_item_and_frees_its_problems(self):
        item = self.item_for(self.a)
        cancel(item.pk)
        self.assertEqual(run_until_idle(self.deps()), [])
        self.assertEqual(self.status(item), PipelineItem.Status.CANCELLED)
        self.item_for(self.a)  # no longer "already in a running pipeline"

    def test_an_unknown_item(self):
        with self.assertRaises(PipelineItem.DoesNotExist):
            pause(99999)
