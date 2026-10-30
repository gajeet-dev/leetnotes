from django.test import override_settings

from pipeline.creation import NothingToDo, create_task
from pipeline.models import ItemStage, Task
from pipeline.stage_plan import ALREADY_FETCHED, REVIEW_OFF
from pipeline.stages import STAGE_ORDER, Stage
from pipeline.task_settings import TaskSettingsError
from pipeline.tests.helpers import make_problem, mark_fetched
from userconfig import update_config
from userconfig.tests.helpers import DataDirDBTestCase


class CreateTaskTests(DataDirDBTestCase):
    def setUp(self):
        super().setUp()
        update_config({"output_dirs": ["/vault"]})
        self.a = make_problem("two-sum", 1)
        self.b = make_problem("contains-duplicate", 217)

    def rows(self, item) -> dict:
        return {row.stage: row for row in item.stages.all()}

    def test_items_follow_the_order_given_and_the_settings_are_snapshotted(self):
        task = create_task([self.b, self.a], {"review": False}).task
        self.assertEqual(
            [item.problem.slug for item in task.items.all()],
            ["contains-duplicate", "two-sum"],
        )
        self.assertEqual(task.status, Task.Status.ACTIVE)
        self.assertFalse(task.settings["review"])
        self.assertEqual(task.settings["output_dirs"], ["/vault"])

    def test_a_new_problem_starts_at_the_first_stage_with_everything_pending(self):
        item = create_task([self.a]).task.items.get()
        self.assertEqual(item.stage, Stage.FETCH_PROBLEM)
        self.assertEqual(item.status, item.Status.QUEUED)
        rows = list(item.stages.all())
        self.assertEqual([row.stage for row in rows], [s.value for s in STAGE_ORDER])
        self.assertTrue(all(row.status == ItemStage.Status.PENDING for row in rows))

    def test_fetched_data_starts_skipped_and_the_item_starts_further_on(self):
        mark_fetched(self.a)
        item = create_task([self.a]).task.items.get()
        rows = self.rows(item)
        for stage in (Stage.FETCH_PROBLEM, Stage.FETCH_IMAGES, Stage.FETCH_SUBMISSION):
            self.assertEqual(
                (rows[stage].status, rows[stage].detail),
                (ItemStage.Status.SKIPPED, ALREADY_FETCHED),
            )
        self.assertEqual(item.stage, Stage.GENERATE_PREFILL)

    def test_review_off_skips_the_review_stage(self):
        item = create_task([self.a], {"review": False}).task.items.get()
        row = self.rows(item)[Stage.REVIEW]
        self.assertEqual(
            (row.status, row.detail), (ItemStage.Status.SKIPPED, REVIEW_OFF)
        )

    def test_problems_that_cannot_join_are_reported_not_added(self):
        unsolved = make_problem("unsolved", 3, solved=False)
        creation = create_task([self.a, unsolved])
        self.assertEqual(creation.task.items.count(), 1)
        self.assertEqual([r.problem.slug for r in creation.rejected], ["unsolved"])

    def test_nothing_eligible_creates_no_task(self):
        unsolved = make_problem("unsolved", 3, solved=False)
        with self.assertRaisesMessage(NothingToDo, "not solved yet"):
            create_task([unsolved])
        self.assertEqual(Task.objects.count(), 0)

    def test_a_problem_cannot_join_two_open_tasks(self):
        create_task([self.a])
        with self.assertRaisesMessage(NothingToDo, "already in a running task"):
            create_task([self.a])
        Task.objects.update(status=Task.Status.CANCELLED)
        create_task([self.a])  # free again once the first task is cancelled

    def test_duplicates_are_collapsed(self):
        self.assertEqual(create_task([self.a, self.a]).task.items.count(), 1)

    def test_names(self):
        self.assertIn("2 problems", create_task([self.a, self.b]).task.name)
        Task.objects.all().delete()
        self.assertEqual(create_task([self.a], name="My run").task.name, "My run")

    def test_a_bad_setting_creates_nothing(self):
        with self.assertRaises(TaskSettingsError):
            create_task([self.a], {"review": "maybe"})
        self.assertEqual(Task.objects.count(), 0)

    @override_settings(NOTES_OUTPUT_DIRS=[])
    def test_an_output_folder_is_required(self):
        update_config({"output_dirs": None})
        with self.assertRaisesMessage(TaskSettingsError, "No output folder"):
            create_task([self.a])
        self.assertEqual(Task.objects.count(), 0)
