import json

from django.test import TestCase
from django.utils import timezone

from prefill.models import AIResponse
from prefill.services import activate, save_response
from prefill.tests.test_parsing import valid
from problems.models import Problem, Submission


class SaveResponseTests(TestCase):
    def setUp(self):
        self.problem = Problem.objects.create(
            slug="two-sum",
            leetcode_id=1,
            title="Two Sum",
            content_remote_markdown="Find two numbers.",
        )
        Submission.objects.create(
            problem=self.problem,
            lang="python3",
            code="pass",
            submission_date=timezone.now(),
        )
        self.good = json.dumps(valid())

    def save(self, text):
        return save_response(self.problem, text, source=AIResponse.Source.MANUAL)

    def active_count(self):
        return AIResponse.objects.filter(problem=self.problem, is_active=True).count()

    def test_valid_becomes_active(self):
        response = self.save(self.good)
        self.assertEqual(response.status, "valid")
        self.assertTrue(response.is_active)
        self.assertEqual(response.parsed["approaches"][0]["name"], "BFS (Kahn)")

    def test_new_valid_replaces_active_and_keeps_history(self):
        first = self.save(self.good)
        second = self.save(self.good)
        first.refresh_from_db()
        self.assertFalse(first.is_active)
        self.assertTrue(second.is_active)
        self.assertEqual(self.active_count(), 1)
        self.assertEqual(AIResponse.objects.count(), 2)

    def test_invalid_is_stored_but_not_active(self):
        response = self.save("not json at all")
        self.assertEqual(response.status, "invalid")
        self.assertFalse(response.is_active)
        self.assertIn("No JSON", response.error)

    def test_invalid_does_not_displace_active(self):
        good = self.save(self.good)
        self.save("garbage")
        good.refresh_from_db()
        self.assertTrue(good.is_active)

    def test_activate_rejects_invalid(self):
        with self.assertRaises(ValueError):
            activate(self.save("garbage"))
