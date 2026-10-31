from pipeline.models import Task, TaskItem
from pipeline.summary import describe_recent_tasks, describe_task
from pipeline.tests.test_engine import EngineTestCase
from pipeline.transitions import pause_task


class SummaryTests(EngineTestCase):
    def test_counts_per_status(self):
        task = self.task_for(self.problem("a", 1), self.problem("b", 2))
        TaskItem.objects.filter(problem__slug="a").update(
            status=TaskItem.Status.COMPLETED
        )
        self.assertEqual(
            describe_task(task), f"{task.name} [active]: 1 queued, 1 completed"
        )

    def test_a_pause_reason_is_shown(self):
        task = self.task_for(self.problem("a", 1))
        pause_task(task, "cookies expired")
        self.assertTrue(describe_task(task).endswith("(cookies expired)"))

    def test_a_task_without_items(self):
        task = Task.objects.create(name="Empty")
        self.assertEqual(describe_task(task), "Empty [active]: no items")

    def test_recent_tasks_newest_first_and_limited(self):
        for number in range(3):
            Task.objects.create(name=f"Task {number}")
        self.assertEqual(
            [line.split(" [")[0] for line in describe_recent_tasks(limit=2)],
            ["Task 2", "Task 1"],
        )
