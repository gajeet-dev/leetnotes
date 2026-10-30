from unittest import mock

from django.test import TestCase

from leetcode.exceptions import AuthError, NotConfigured, NotFoundError, RateLimitError
from problems.models import Problem, SyncTask
from problems.services.sync import HANDLERS, run_part


class RunPartTests(TestCase):
    def setUp(self):
        self.problem = Problem.objects.create(slug="two-sum", leetcode_id=1)
        self.part = SyncTask.Part.CONTENT

    def sync_task(self) -> SyncTask:
        return SyncTask.objects.get(problem=self.problem, part=self.part)

    def run_with(self, handler):
        with mock.patch.dict(HANDLERS, {self.part: handler}):
            run_part(self.problem, self.part, "client", "downloader")

    def test_creates_the_sync_task_if_missing_and_calls_the_handler(self):
        calls = []
        self.run_with(
            lambda task, deps: calls.append(
                (task.problem_id, deps.client, deps.downloader)
            )
        )
        self.assertEqual(calls, [(self.problem.pk, "client", "downloader")])
        self.assertEqual(self.sync_task().attempts, 1)

    def test_runs_even_if_the_part_is_already_done(self):
        SyncTask.objects.create(
            problem=self.problem, part=self.part, status=SyncTask.Status.DONE
        )
        calls = []
        self.run_with(lambda task, deps: calls.append(1))
        self.assertEqual(calls, [1])

    def test_a_problem_specific_error_marks_the_task_failed_and_is_raised(self):
        def handler(task, deps):
            raise NotFoundError("gone")

        with self.assertRaises(NotFoundError):
            self.run_with(handler)
        self.assertEqual(self.sync_task().status, SyncTask.Status.FAILED)
        self.assertIn("gone", self.sync_task().last_error)

    def test_account_level_errors_do_not_mark_the_task_failed(self):
        for error in (
            AuthError("403"),
            RateLimitError("429"),
            NotConfigured("no cookies"),
        ):
            with self.subTest(error=type(error).__name__):

                def handler(task, deps, error=error):
                    raise error

                with self.assertRaises(type(error)):
                    self.run_with(handler)
                self.assertNotEqual(self.sync_task().status, SyncTask.Status.FAILED)
