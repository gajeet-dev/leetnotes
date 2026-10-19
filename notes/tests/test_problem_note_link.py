from django.test import TestCase

from notes.services import render_problem_note
from problems.models import Problem


class SolutionLinkTests(TestCase):
    def test_link_is_always_present_even_without_a_solution_note(self):
        problem = Problem.objects.create(
            slug="two-sum",
            leetcode_id=1,
            title="Two Sum",
            difficulty="Easy",
            url="https://leetcode.com/problems/two-sum/",
            tags=[],
            content_local_markdown="Find two numbers.",
        )
        self.assertIn("[[0001-two-sum-solution|Link]]", render_problem_note(problem))
