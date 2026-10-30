from unittest import mock

from django.test import TestCase

from leetcode.exceptions import AuthError
from pipeline.handlers import StageDeps, fetch_images, fetch_problem, fetch_submission
from pipeline.models import ItemStage, Task, TaskItem
from pipeline.outcomes import StageError
from pipeline.task_settings import TaskSettings
from pipeline.tests.helpers import make_problem
from problems.models import Problem, SyncTask

DEPS = StageDeps(get_client=lambda: "client", get_downloader=lambda: "downloader")


def make_item(problem: Problem) -> TaskItem:
    return TaskItem.objects.create(task=Task.objects.create(), problem=problem)


class FetchHandlerTests(TestCase):
    def setUp(self):
        self.problem = make_problem("two-sum", 1, raw_question_html="<p>x</p>")
        self.item = make_item(self.problem)
        self.settings = TaskSettings()

    def run_handler(self, handler):
        with mock.patch("pipeline.handlers.run_part") as run_part:
            outcome = handler(self.item, self.settings, DEPS)
        return outcome, run_part

    def test_each_fetch_stage_fetches_its_own_part(self):
        cases = [
            (fetch_problem, SyncTask.Part.CONTENT),
            (fetch_images, SyncTask.Part.IMAGES),
            (fetch_submission, SyncTask.Part.SUBMISSIONS),
        ]
        for handler, part in cases:
            with self.subTest(part=part):
                outcome, run_part = self.run_handler(handler)
                run_part.assert_called_once()
                problem, *rest = run_part.call_args.args
                self.assertEqual(problem.pk, self.problem.pk)
                self.assertEqual(rest, [part, "client", "downloader"])
                self.assertEqual(outcome.status, ItemStage.Status.DONE)

    def test_the_problem_is_loaded_fresh_so_earlier_stages_are_seen(self):
        Problem.objects.filter(pk=self.problem.pk).update(title="renamed")
        _, run_part = self.run_handler(fetch_problem)
        self.assertEqual(run_part.call_args.args[0].title, "renamed")

    def test_images_need_the_statement(self):
        Problem.objects.filter(pk=self.problem.pk).update(raw_question_html=None)
        with self.assertRaisesMessage(StageError, "statement"):
            self.run_handler(fetch_images)

    def test_leetcode_errors_propagate_to_the_engine(self):
        with mock.patch("pipeline.handlers.run_part", side_effect=AuthError("403")):
            with self.assertRaises(AuthError):
                fetch_problem(self.item, self.settings, DEPS)
