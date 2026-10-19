import json

from django.test import TestCase

from notes.solution_notes import SolutionNoteUnavailable, render_solution_note
from prefill.models import AIResponse
from prefill.services import save_response
from prefill.tests.test_parsing import valid
from problems.models import Problem


def two_approaches() -> dict:
    data = valid()
    second = {
        **data["approaches"][0],
        "name": "DFS (cycle detection)",
        "code_ref": "DfsCycleDetection",
    }
    data["approaches"].append(second)
    return data


class RenderSolutionNoteTests(TestCase):
    def setUp(self):
        self.problem = Problem.objects.create(
            slug="course-schedule",
            leetcode_id=207,
            title="Course Schedule",
            difficulty="Medium",
            url="https://leetcode.com/problems/course-schedule/",
            tags=[{"name": "Graph", "slug": "graph"}],
        )

    def save(self, data: dict):
        save_response(
            self.problem,
            json.dumps(data),
            source=AIResponse.Source.MANUAL,
            prompt=_FakePrompt(),
        )

    def test_front_matter_and_header(self):
        self.save(valid())
        text = render_solution_note(self.problem)
        self.assertTrue(text.startswith("---\n"))
        for expected in (
            "id: 207\n",
            "slug: course-schedule\n",
            " - graph\n",
            "difficulty: Medium\n",
        ):
            self.assertIn(expected, text)
        self.assertIn("# 207. Course Schedule", text)

    def test_links_back_to_the_problem_note(self):
        self.save(valid())
        self.assertIn(
            "[[0207-course-schedule|Link]]", render_solution_note(self.problem)
        )

    def test_summary_is_a_blockquote(self):
        self.save(valid())
        self.assertIn(
            "> Can all courses be finished?", render_solution_note(self.problem)
        )

    def test_single_approach_is_flat(self):
        self.save(valid())
        text = render_solution_note(self.problem)
        self.assertIn("**Core idea:**", text)
        self.assertNotIn("## Approaches", text)

    def test_two_approaches_get_headings_and_a_comparison_table(self):
        self.save(two_approaches())
        text = render_solution_note(self.problem)
        self.assertIn("## Approaches", text)
        self.assertIn("### 1. BFS (Kahn)", text)
        self.assertIn("### 2. DFS (cycle detection)", text)
        self.assertIn("| Approach | Time | Space | Pick when |", text)

    def test_no_active_note(self):
        with self.assertRaisesMessage(SolutionNoteUnavailable, "no active AI note"):
            render_solution_note(self.problem)

    def test_no_leetcode_id(self):
        self.problem.leetcode_id = None
        self.problem.save()
        with self.assertRaisesMessage(SolutionNoteUnavailable, "no LeetCode id"):
            render_solution_note(self.problem)


class _FakePrompt:
    """Stands in for a built prompt, so these tests don't need problem content."""

    version = "test"
    input_hash = "test"
