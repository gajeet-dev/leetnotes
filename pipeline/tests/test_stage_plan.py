import json

from django.test import TestCase
from django.utils import timezone

from pipeline.stage_plan import (
    AI_NOTE_EXISTS,
    ALREADY_FETCHED,
    EXISTING_AI_NOTE,
    PINNED_KEPT,
    REVIEW_OFF,
    SOLUTION_CHANGED,
    initial_stages,
)
from pipeline.stages import STAGE_ORDER, Stage
from pipeline.task_settings import TaskSettings
from prefill.models import AIResponse
from prefill.services import save_response
from prefill.tests.test_parsing import valid
from problems.models import Problem, Submission, SyncTask


class StagePlanTests(TestCase):
    def setUp(self):
        self.problem = Problem.objects.create(
            slug="two-sum", leetcode_id=1, title="Two Sum", solved=True,
            content_remote_markdown="Find two numbers.",
        )
        self.submission = Submission.objects.create(
            problem=self.problem, lang="python3", code="pass",
            submission_date=timezone.now(),
        )

    def mark_fetched(self, *parts):
        for part in parts or SyncTask.Part.values:
            SyncTask.objects.update_or_create(
                problem=self.problem, part=part,
                defaults={"status": SyncTask.Status.DONE},
            )

    def add_ai_note(self):
        save_response(self.problem, json.dumps(valid()), source=AIResponse.Source.MANUAL)

    def plan(self, **options):
        return initial_stages(self.problem, TaskSettings(**options))

    def test_the_stages_come_back_in_pipeline_order(self):
        self.assertEqual(list(self.plan()), list(STAGE_ORDER))

    def test_a_new_problem_needs_every_stage(self):
        plan = self.plan()
        self.assertTrue(all(not start.skipped for start in plan.values()))

    def test_fetched_data_starts_skipped_with_a_reason(self):
        self.mark_fetched()
        plan = self.plan()
        for stage in (Stage.FETCH_PROBLEM, Stage.FETCH_IMAGES, Stage.FETCH_SUBMISSION):
            self.assertTrue(plan[stage].skipped)
            self.assertEqual(plan[stage].detail, ALREADY_FETCHED)
        self.assertFalse(plan[Stage.GENERATE_PREFILL].skipped)

    def test_images_are_redone_when_the_content_is_not_fetched(self):
        self.mark_fetched(SyncTask.Part.IMAGES)
        plan = self.plan()
        self.assertFalse(plan[Stage.FETCH_PROBLEM].skipped)
        self.assertFalse(plan[Stage.FETCH_IMAGES].skipped)

    def test_refetch_redoes_everything(self):
        self.mark_fetched()
        plan = self.plan(refetch=True)
        for stage in (Stage.FETCH_PROBLEM, Stage.FETCH_IMAGES, Stage.FETCH_SUBMISSION):
            self.assertFalse(plan[stage].skipped)

    def test_refetch_keeps_a_pinned_solution(self):
        self.mark_fetched()
        self.submission.pinned = True
        self.submission.save()
        plan = self.plan(refetch=True)
        self.assertEqual(plan[Stage.FETCH_SUBMISSION].detail, PINNED_KEPT)
        self.assertTrue(plan[Stage.FETCH_SUBMISSION].skipped)
        self.assertFalse(plan[Stage.FETCH_PROBLEM].skipped)

    def test_a_current_ai_note_skips_prefill_and_review(self):
        self.mark_fetched()
        self.add_ai_note()
        plan = self.plan()
        self.assertEqual(plan[Stage.GENERATE_PREFILL].detail, AI_NOTE_EXISTS)
        self.assertTrue(plan[Stage.GENERATE_PREFILL].skipped)
        self.assertEqual(plan[Stage.REVIEW].detail, EXISTING_AI_NOTE)
        self.assertTrue(plan[Stage.REVIEW].skipped)

    def test_regenerating_forces_prefill_and_review(self):
        self.mark_fetched()
        self.add_ai_note()
        plan = self.plan(regenerate_prefill=True)
        self.assertFalse(plan[Stage.GENERATE_PREFILL].skipped)
        self.assertFalse(plan[Stage.REVIEW].skipped)

    def test_a_stale_ai_note_is_regenerated_with_a_reason(self):
        self.mark_fetched()
        self.add_ai_note()
        self.submission.code = "a different solution"
        self.submission.save()
        start = self.plan()[Stage.GENERATE_PREFILL]
        self.assertFalse(start.skipped)
        self.assertEqual(start.detail, SOLUTION_CHANGED)

    def test_refetching_the_data_regenerates_the_ai_note(self):
        self.mark_fetched()
        self.add_ai_note()
        self.assertFalse(self.plan(refetch=True)[Stage.GENERATE_PREFILL].skipped)

    def test_review_off_skips_the_review_stage(self):
        start = self.plan(review=False)[Stage.REVIEW]
        self.assertTrue(start.skipped)
        self.assertEqual(start.detail, REVIEW_OFF)

    def test_saving_notes_is_always_pending(self):
        self.mark_fetched()
        self.add_ai_note()
        self.assertFalse(self.plan()[Stage.SAVE_NOTES].skipped)
