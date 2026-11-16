from django.test import override_settings

from pipeline.models import ItemStage, PipelineItem
from pipeline.stages.definitions import STAGE_ORDER, Stage
from pipeline.stages.plan import ALREADY_FETCHED, REVIEW_OFF
from pipeline.tasks.creation import NothingToDo, start_pipeline_items
from pipeline.tasks.settings import TaskSettingsError
from pipeline.tests.helpers import make_problem, mark_fetched
from userconfig import update_config
from userconfig.tests.helpers import DataDirDBTestCase


class CreatePipelineTests(DataDirDBTestCase):
    def setUp(self):
        super().setUp()
        update_config({"output_dirs": ["/vault"]})
        self.a = make_problem("two-sum", 1)
        self.b = make_problem("contains-duplicate", 217)

    def rows(self, item) -> dict:
        return {row.stage: row for row in item.stages.all()}

    def test_items_follow_the_order_given_and_the_settings_are_snapshotted(self):
        result = start_pipeline_items([self.b, self.a], {"review": False}, wake=None)
        items = result.items
        self.assertEqual(
            [item.problem.slug for item in items],
            ["contains-duplicate", "two-sum"],
        )
        self.assertEqual(items[0].status, PipelineItem.Status.QUEUED)
        self.assertFalse(items[0].settings["review"])
        self.assertEqual(items[0].settings["output_dirs"], ["/vault"])

    def test_a_new_problem_starts_at_the_first_stage_with_everything_pending(self):
        item = start_pipeline_items([self.a], wake=None).items[0]
        self.assertEqual(item.stage, Stage.FETCH_PROBLEM)
        self.assertEqual(item.status, PipelineItem.Status.QUEUED)
        rows = list(item.stages.all())
        self.assertEqual([row.stage for row in rows], [s.value for s in STAGE_ORDER])
        self.assertTrue(all(row.status == ItemStage.Status.PENDING for row in rows))

    def test_fetched_data_starts_skipped_and_the_item_starts_further_on(self):
        mark_fetched(self.a)
        item = start_pipeline_items([self.a], wake=None).items[0]
        rows = self.rows(item)
        for stage in (Stage.FETCH_PROBLEM, Stage.FETCH_IMAGES, Stage.FETCH_SUBMISSION):
            self.assertEqual(
                (rows[stage].status, rows[stage].detail),
                (ItemStage.Status.SKIPPED, ALREADY_FETCHED),
            )
        self.assertEqual(item.stage, Stage.GENERATE_PREFILL)

    def test_review_off_skips_the_review_stage(self):
        item = start_pipeline_items([self.a], {"review": False}, wake=None).items[0]
        row = self.rows(item)[Stage.REVIEW]
        self.assertEqual(
            (row.status, row.detail), (ItemStage.Status.SKIPPED, REVIEW_OFF)
        )

    def test_problems_that_cannot_join_are_reported_not_added(self):
        unsolved = make_problem("unsolved", 3, solved=False)
        creation = start_pipeline_items([self.a, unsolved], wake=None)
        self.assertEqual(len(creation.items), 1)
        self.assertEqual([r.problem.slug for r in creation.rejected], ["unsolved"])

    def test_nothing_eligible_creates_no_items(self):
        unsolved = make_problem("unsolved", 3, solved=False)
        with self.assertRaisesMessage(NothingToDo, "not solved yet"):
            start_pipeline_items([unsolved], wake=None)
        self.assertEqual(PipelineItem.objects.count(), 0)

    def test_duplicates_are_collapsed(self):
        items = start_pipeline_items([self.a, self.a], wake=None).items
        self.assertEqual(len(items), 1)

    def test_a_bad_setting_creates_nothing(self):
        with self.assertRaises(TaskSettingsError):
            start_pipeline_items([self.a], {"review": "maybe"}, wake=None)
        self.assertEqual(PipelineItem.objects.count(), 0)

    def test_an_output_folder_is_required(self):
        with self.assertRaisesMessage(TaskSettingsError, "No output folder"):
            start_pipeline_items([self.a], {"output_dirs": []}, wake=None)
        self.assertEqual(PipelineItem.objects.count(), 0)
