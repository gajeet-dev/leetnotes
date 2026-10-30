from django.test import TestCase

from pipeline.eligibility import split_eligible
from pipeline.models import Task, TaskItem
from pipeline.tests.helpers import make_problem


class EligibilityTests(TestCase):
    def split(self, *problems):
        eligible, rejected = split_eligible(list(problems))
        return [p.slug for p in eligible], {r.problem.slug: r.reason for r in rejected}

    def test_a_solved_problem_is_eligible(self):
        self.assertEqual(self.split(make_problem("a", 1)), (["a"], {}))

    def test_unsolved_is_rejected(self):
        _, rejected = self.split(make_problem("a", 1, solved=False))
        self.assertEqual(rejected, {"a": "not solved yet"})

    def test_no_leetcode_id_is_rejected(self):
        _, rejected = self.split(make_problem("a", None))
        self.assertIn("a", rejected)

    def test_premium_without_a_statement_is_rejected(self):
        _, rejected = self.split(
            make_problem("a", 1, paid_only=True, content_remote_markdown=None)
        )
        self.assertIn("premium", rejected["a"])

    def test_premium_with_a_statement_is_eligible(self):
        self.assertEqual(self.split(make_problem("a", 1, paid_only=True))[0], ["a"])

    def test_the_order_is_kept(self):
        first, second = make_problem("a", 1), make_problem("b", 2)
        self.assertEqual(self.split(second, first)[0], ["b", "a"])

    def test_when_a_problem_is_busy(self):
        cases = [
            (Task.Status.ACTIVE, TaskItem.Status.QUEUED, True),
            (Task.Status.ACTIVE, TaskItem.Status.RUNNING, True),
            (Task.Status.ACTIVE, TaskItem.Status.NEEDS_INPUT, True),
            (Task.Status.PAUSED, TaskItem.Status.QUEUED, True),
            (Task.Status.ACTIVE, TaskItem.Status.COMPLETED, False),
            (Task.Status.ACTIVE, TaskItem.Status.FAILED, False),
            (Task.Status.CANCELLED, TaskItem.Status.QUEUED, False),
            (Task.Status.FINISHED, TaskItem.Status.QUEUED, False),
        ]
        for number, (task_status, item_status, busy) in enumerate(cases, start=1):
            with self.subTest(task=task_status, item=item_status):
                problem = make_problem(f"p{number}", number)
                task = Task.objects.create(status=task_status)
                TaskItem.objects.create(task=task, problem=problem, status=item_status)
                _, rejected = self.split(problem)
                self.assertEqual(
                    rejected.get(problem.slug) == "already in a running task", busy
                )
