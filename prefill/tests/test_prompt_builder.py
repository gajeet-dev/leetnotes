from django.test import TestCase
from django.utils import timezone

from prefill.prompt_builder import PromptInputError, build_prompt
from problems.models import Problem, Submission


class BuildPromptTests(TestCase):
    def setUp(self):
        self.problem = Problem.objects.create(
            slug="two-sum",
            leetcode_id=1,
            title="Two Sum",
            difficulty="Easy",
            tags=[{"name": "Array", "slug": "array"}],
            content_remote_markdown="Find two numbers.\n\n![fig](https://x/y.png)",
        )
        self.submission = Submission.objects.create(
            problem=self.problem,
            lang="python3",
            code="class Solution:\n    pass\n",
            submission_date=timezone.now(),
        )

    def test_contains_problem_and_code(self):
        prompt = build_prompt(self.problem)
        self.assertIn("1. Two Sum", prompt.user)
        self.assertIn("class Solution:", prompt.user)
        self.assertIn("Tags: Array", prompt.user)
        self.assertIn('"approaches"', prompt.system)  # schema was embedded

    def test_images_are_replaced(self):
        prompt = build_prompt(self.problem)
        self.assertIn("[image omitted]", prompt.user)
        self.assertNotIn("https://x/y.png", prompt.user)

    def test_input_hash_tracks_code(self):
        before = build_prompt(self.problem)
        self.assertEqual(before.input_hash, build_prompt(self.problem).input_hash)
        self.submission.code = "changed"
        self.submission.save()
        self.assertNotEqual(before.input_hash, build_prompt(self.problem).input_hash)

    def test_requires_content(self):
        self.problem.content_remote_markdown = None
        self.problem.save()
        with self.assertRaises(PromptInputError):
            build_prompt(self.problem)

    def test_requires_submission(self):
        self.submission.delete()
        with self.assertRaises(PromptInputError):
            build_prompt(self.problem)
