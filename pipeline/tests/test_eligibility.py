from django.test import TestCase

from pipeline.models import PipelineItem
from pipeline.tasks.eligibility import split_eligible
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
            (PipelineItem.Status.QUEUED, True),
            (PipelineItem.Status.RUNNING, True),
            (PipelineItem.Status.NEEDS_INPUT, True),
            (PipelineItem.Status.PAUSED, False),
            (PipelineItem.Status.COMPLETED, False),
            (PipelineItem.Status.FAILED, False),
            (PipelineItem.Status.CANCELLED, False),
        ]
        for number, (item_status, busy) in enumerate(cases, start=1):
            with self.subTest(item=item_status):
                problem = make_problem(f"p{number}", number)
                PipelineItem.objects.create(problem=problem, status=item_status)
                _, rejected = self.split(problem)
                self.assertEqual(
                    rejected.get(problem.slug) == "already running in the pipeline",
                    busy,
                )
