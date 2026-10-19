import json

from django.test import TestCase
from django.utils import timezone

from notes.context import active_solution_note, build_solution_context
from notes.naming import (
    problem_note_filename,
    solution_note_filename,
)
from prefill.models import AIResponse
from prefill.services import save_response
from prefill.tests.test_parsing import valid
from problems.models import Problem, Submission

TEMPLATE_VARIABLES = {
    "frontend_id",
    "slug",
    "title",
    "difficulty",
    "url",
    "tags",
    "problem_note",
    "problem_summary",
    "recognition_clue",
    "approaches",
    "takeaway",
    "aliases",
    "related",
    "flashcards",
}


class SolutionContextTests(TestCase):
    def setUp(self):
        self.problem = Problem.objects.create(
            slug="course-schedule",
            leetcode_id=207,
            title="Course Schedule",
            difficulty="Medium",
            url="https://leetcode.com/problems/course-schedule/",
            tags=[{"name": "Graph", "slug": "graph"}],
            content_remote_markdown="Can you finish?",
        )
        Submission.objects.create(
            problem=self.problem,
            lang="java",
            code="class Solution {}",
            submission_date=timezone.now(),
        )

    def save(self, text):
        return save_response(self.problem, text, source=AIResponse.Source.MANUAL)

    def test_no_response_means_no_note(self):
        self.assertIsNone(active_solution_note(self.problem))

    def test_invalid_response_means_no_note(self):
        self.save("garbage")
        self.assertIsNone(active_solution_note(self.problem))

    def test_active_note_is_loaded(self):
        self.save(json.dumps(valid()))
        note = active_solution_note(self.problem)
        self.assertEqual(note.approaches[0].name, "BFS (Kahn)")

    def test_context_has_every_template_variable(self):
        self.save(json.dumps(valid()))
        context = build_solution_context(
            self.problem, active_solution_note(self.problem)
        )
        self.assertTrue(TEMPLATE_VARIABLES <= set(context))

    def test_tags_are_slugs_and_link_points_at_the_problem_note(self):
        self.save(json.dumps(valid()))
        context = build_solution_context(
            self.problem, active_solution_note(self.problem)
        )
        self.assertEqual(context["tags"], ["graph"])
        self.assertEqual(context["problem_note"], "0207-course-schedule")

    def test_file_names(self):
        self.assertEqual(problem_note_filename(self.problem), "0207-course-schedule.md")
        self.assertEqual(
            solution_note_filename(self.problem), "0207-course-schedule-solution.md"
        )
