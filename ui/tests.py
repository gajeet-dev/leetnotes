from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from notes.models import NoteRecord
from pipeline.models import ItemStage, Task, TaskItem
from pipeline.stages.definitions import Stage
from problems.models import Problem, Submission, SyncTask
from problems.services.sync import DiscoveryResult, RecentCheckResult


class ProblemsListViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        for i in range(35):
            Problem.objects.create(
                slug=f"problem-{i}",
                leetcode_id=i + 1,
                title=f"Problem {i}",
                difficulty=Problem.Difficulty.EASY if i % 2 == 0 else Problem.Difficulty.HARD,
                solved=i < 10,
            )

    def test_problems_list_full_page(self):
        url = reverse("ui:problems")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "ui/problems.html")
        self.assertTemplateUsed(response, "ui/partials/problems_table.html")
        self.assertEqual(len(response.context["page_obj"]), 25)
        self.assertEqual(response.context["total_count"], 35)

    def test_problems_list_htmx_partial(self):
        url = reverse("ui:problems")
        response = self.client.get(url, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "ui/partials/problems_table.html")
        self.assertTemplateNotUsed(response, "ui/problems.html")

    def test_problems_list_pagination(self):
        url = reverse("ui:problems")
        response = self.client.get(url, {"page": 2})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["page_obj"]), 10)

    def test_problems_list_search_and_filter(self):
        url = reverse("ui:problems")
        response = self.client.get(url, {"difficulty": "Hard"})
        self.assertEqual(response.status_code, 200)
        for p in response.context["page_obj"]:
            self.assertEqual(p.difficulty, "Hard")


class DashboardViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.p1 = Problem.objects.create(
            slug="two-sum",
            leetcode_id=1,
            title="Two Sum",
            difficulty=Problem.Difficulty.EASY,
            solved=True,
        )
        self.p2 = Problem.objects.create(
            slug="add-two-numbers",
            leetcode_id=2,
            title="Add Two Numbers",
            difficulty=Problem.Difficulty.MEDIUM,
            solved=True,
        )

        self.task = Task.objects.create(name="Generate notes", status=Task.Status.ACTIVE)
        self.item = TaskItem.objects.create(
            task=self.task, problem=self.p1, position=0, status=TaskItem.Status.RUNNING
        )
        ItemStage.objects.create(
            item=self.item, stage=Stage.FETCH_PROBLEM, status=ItemStage.Status.DONE
        )

    def test_dashboard_renders(self):
        url = reverse("ui:dashboard")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "ui/dashboard.html")
        self.assertEqual(response.context["solved_count"], 2)
        self.assertIsNotNone(response.context["task_stats"])
        self.assertEqual(response.context["task_stats"]["total_items"], 1)

    def test_tasks_progress_partial(self):
        url = reverse("ui:tasks_progress")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "ui/partials/dashboard_task_card.html")

    def test_task_detail_view(self):
        url = reverse("ui:task_detail", args=[self.task.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "ui/task_detail.html")
        self.assertEqual(len(response.context["items"]), 1)

    @patch("ui.views.refresh_recent_submissions")
    @patch("ui.views.discover")
    @patch("ui.views.get_client")
    def test_sync_leetcode_button(self, mock_client, mock_disc, mock_refresh):
        mock_disc.return_value = DiscoveryResult(total=100, new_problems=2, newly_solved=1, new_tasks=4)
        mock_refresh.return_value = RecentCheckResult(checked=20, reset=1, newly_solved=0, up_to_date=19)

        url = reverse("ui:sync_leetcode")
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "ui/partials/sync_response.html")
        self.assertTemplateUsed(response, "ui/partials/notes_library_stats.html")
        self.assertIn("Sync complete", response.context["message"])


class GenerateNotesViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.problems = []
        for i in range(15):
            p = Problem.objects.create(
                slug=f"solved-prob-{i}",
                leetcode_id=i + 1,
                title=f"Solved Problem {i}",
                difficulty=Problem.Difficulty.EASY if i % 2 == 0 else Problem.Difficulty.HARD,
                category=Problem.Category.ALGORITHMS,
                solved=True,
            )
            self.problems.append(p)

    def test_generate_notes_page_renders_only_solved_algorithms_problems(self):
        # Create an unsolved algorithms problem
        Problem.objects.create(
            slug="unsolved-algo",
            leetcode_id=998,
            title="Unsolved Algo",
            difficulty=Problem.Difficulty.HARD,
            category=Problem.Category.ALGORITHMS,
            solved=False,
        )
        # Create a solved database problem (should be excluded!)
        Problem.objects.create(
            slug="solved-db",
            leetcode_id=999,
            title="Solved Database Problem",
            difficulty=Problem.Difficulty.EASY,
            category=Problem.Category.DATABASE,
            solved=True,
        )

        url = reverse("ui:generate_notes")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "ui/generate_notes.html")
        self.assertTemplateUsed(response, "ui/partials/generate_table.html")
        self.assertEqual(response.context["total_solved_count"], 15)
        for prob in response.context["page_obj"]:
            self.assertTrue(prob.solved)
            self.assertEqual(prob.category, Problem.Category.ALGORITHMS)

    def test_generate_notes_htmx_partial(self):
        url = reverse("ui:generate_notes")
        response = self.client.get(url, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "ui/partials/generate_table.html")
        self.assertTemplateNotUsed(response, "ui/generate_notes.html")

    def test_suggested_problems_sorted_by_submission_date_desc(self):
        now = timezone.now()
        # Add submissions with distinct dates
        Submission.objects.create(
            problem=self.problems[0],
            lang="python3",
            code="pass",
            submission_date=now - timedelta(days=5),
        )
        Submission.objects.create(
            problem=self.problems[1],
            lang="python3",
            code="pass",
            submission_date=now - timedelta(hours=2),  # Most recent
        )
        Submission.objects.create(
            problem=self.problems[2],
            lang="python3",
            code="pass",
            submission_date=now - timedelta(days=1),   # Second most recent
        )

        url = reverse("ui:generate_notes")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        suggested = response.context["suggested_problems"]
        self.assertEqual(len(suggested), 3)
        # Problems[1] should be first (2 hours ago)
        self.assertEqual(suggested[0].id, self.problems[1].id)
        # Problems[2] should be second (1 day ago)
        self.assertEqual(suggested[1].id, self.problems[2].id)
        # Problems[0] should be third (5 days ago)
        self.assertEqual(suggested[2].id, self.problems[0].id)

    def test_suggested_problems_excluded_from_other_solved(self):
        now = timezone.now()
        Submission.objects.create(
            problem=self.problems[0],
            lang="python3",
            code="pass",
            submission_date=now,
        )

        url = reverse("ui:generate_notes")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        suggested_ids = [p.id for p in response.context["suggested_problems"]]
        other_ids = [p.id for p in response.context["page_obj"]]

        self.assertIn(self.problems[0].id, suggested_ids)
        self.assertNotIn(self.problems[0].id, other_ids)

    def test_filter_by_difficulty(self):
        url = reverse("ui:generate_notes")
        response = self.client.get(url, {"difficulty": "Easy"})
        self.assertEqual(response.status_code, 200)
        for prob in response.context["page_obj"]:
            self.assertEqual(prob.difficulty, Problem.Difficulty.EASY)


class TaskCreationAndDetailTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.p1 = Problem.objects.create(
            slug="prob-one",
            leetcode_id=1,
            title="Problem One",
            difficulty=Problem.Difficulty.EASY,
            category=Problem.Category.ALGORITHMS,
            solved=True,
        )
        self.p2 = Problem.objects.create(
            slug="prob-two",
            leetcode_id=2,
            title="Problem Two",
            difficulty=Problem.Difficulty.MEDIUM,
            category=Problem.Category.ALGORITHMS,
            solved=True,
        )
        self.sub = Submission.objects.create(
            problem=self.p1,
            lang="python3",
            code="def two_sum(): pass",
            submission_date=timezone.now(),
            pinned=False,
        )

    def test_task_preview_modal(self):
        url = reverse("ui:task_preview_modal")
        response = self.client.post(url, {"problem_ids": [self.p1.id, self.p2.id]})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "ui/partials/task_preview_modal.html")
        self.assertIn("Configure Generation Task", response.content.decode())
        self.assertEqual(len(response.context["problems"]), 2)
        self.assertEqual(response.context["preview"].eligible, 2)

    @patch("ui.views.ensure_worker_running")
    def test_create_task_action(self, mock_worker):
        url = reverse("ui:create_task")
        data = {
            "problem_ids": [self.p1.id, self.p2.id],
            "name": "Custom Test Task",
            "provider": "manual",
            "review": "on",
            "refetch_toggle": "on",
            "refetch_submission": "on",
            "replace_edited": "on",
        }
        response = self.client.post(url, data, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.has_header("HX-Redirect"))
        self.assertTrue(mock_worker.called)

        task = Task.objects.get(name="Custom Test Task")
        self.assertEqual(task.items.count(), 2)
        self.assertTrue(task.settings["review"])
        self.assertTrue(task.settings["refetch_submission"])
        self.assertTrue(task.settings["replace_edited"])

    def test_toggle_submission_pin(self):
        self.assertFalse(self.sub.pinned)
        url = reverse("ui:toggle_submission_pin", args=[self.sub.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        self.sub.refresh_from_db()
        self.assertTrue(self.sub.pinned)

        # Toggle again
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        self.sub.refresh_from_db()
        self.assertFalse(self.sub.pinned)

    def test_task_detail_view(self):
        from pipeline.tasks.creation import create_task
        creation = create_task([self.p1], overrides={"provider": "manual"})
        task = creation.task

        url = reverse("ui:task_detail", args=[task.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "ui/task_detail.html")
        self.assertTemplateUsed(response, "ui/partials/task_detail_content.html")
        self.assertTemplateUsed(response, "ui/partials/task_item_card.html")
        self.assertEqual(response.context["total_items"], 1)

    def test_task_cancel_action(self):
        from pipeline.tasks.creation import create_task
        creation = create_task([self.p1], overrides={"provider": "manual"})
        task = creation.task
        self.assertEqual(task.status, Task.Status.ACTIVE)

        url = reverse("ui:task_cancel", args=[task.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)

        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.CANCELLED)

    def test_task_cancel_action_htmx(self):
        from pipeline.tasks.creation import create_task
        creation = create_task([self.p1], overrides={"provider": "manual"})
        task = creation.task

        url = reverse("ui:task_cancel", args=[task.id])
        response = self.client.post(url, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "ui/partials/task_control_response.html")
        self.assertTemplateNotUsed(response, "ui/task_detail.html")

        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.CANCELLED)

    def test_task_pin_all_submissions(self):
        from pipeline.tasks.creation import create_task
        creation = create_task([self.p1, self.p2], overrides={"provider": "manual"})
        task = creation.task
        sub2 = Submission.objects.create(
            problem=self.p2,
            lang="python3",
            submission_date=timezone.now(),
            code="def two_sum(): pass",
            pinned=False,
        )
        self.assertFalse(self.sub.pinned)
        self.assertFalse(sub2.pinned)

        # 1. First call: at least one unpinned -> pins all
        url = reverse("ui:task_pin_all", args=[task.id])
        response = self.client.post(url, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "ui/partials/task_pin_response.html")

        self.sub.refresh_from_db()
        sub2.refresh_from_db()
        self.assertTrue(self.sub.pinned)
        self.assertTrue(sub2.pinned)

        # 2. Second call: all are pinned -> unpins all
        response = self.client.post(url, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "ui/partials/task_pin_response.html")
        self.sub.refresh_from_db()
        sub2.refresh_from_db()
        self.assertFalse(self.sub.pinned)
        self.assertFalse(sub2.pinned)

    def test_task_batch_pin_submissions(self):
        from pipeline.tasks.creation import create_task
        creation = create_task([self.p1, self.p2], overrides={"provider": "manual"})
        task = creation.task
        item1 = task.items.get(problem=self.p1)
        item2 = task.items.get(problem=self.p2)

        sub2 = Submission.objects.create(
            problem=self.p2,
            lang="python3",
            submission_date=timezone.now(),
            code="def two_sum(): pass",
            pinned=False,
        )

        url = reverse("ui:task_batch_pin", args=[task.id])
        # Pin only item1
        response = self.client.post(url, {"item_ids": [item1.id], "pin_action": "pin"}, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.sub.refresh_from_db()
        sub2.refresh_from_db()
        self.assertTrue(self.sub.pinned)
        self.assertFalse(sub2.pinned)

        # Unpin item1
        response = self.client.post(url, {"item_ids": [item1.id], "pin_action": "unpin"}, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.sub.refresh_from_db()
        self.assertFalse(self.sub.pinned)

    def test_task_live_status_endpoint(self):
        from pipeline.tasks.creation import create_task
        creation = create_task([self.p1], overrides={"provider": "manual"})
        task = creation.task
        url = reverse("ui:task_live_status", args=[task.id])
        response = self.client.get(url, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "ui/partials/task_live_updates.html")

    def test_task_item_manual_input_htmx(self):
        from pipeline.tasks.creation import create_task
        creation = create_task([self.p1], overrides={"provider": "manual"})
        task = creation.task
        item = task.items.first()
        url = reverse("ui:item_manual_input", args=[item.id])
        # Submit invalid json first
        response = self.client.post(url, {"reply_text": "invalid json"}, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "ui/partials/task_item_action_response.html")
        self.assertContains(response, "task-live-poller")
        self.assertContains(response, "item-status-pill-")
        self.assertContains(response, "item-progress-section-")

    def test_tasks_list_view(self):
        from pipeline.tasks.creation import create_task
        create_task([self.p1], overrides={"provider": "manual"})
        url = reverse("ui:tasks_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "ui/tasks_list.html")
        self.assertGreaterEqual(response.context["total_tasks_count"], 1)
        self.assertTrue(response.context["has_active_tasks"])

    def test_tasks_live_status_view(self):
        from pipeline.tasks.creation import create_task
        creation = create_task([self.p1], overrides={"provider": "manual"})
        url = reverse("ui:tasks_live_status")
        response = self.client.get(url, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "ui/partials/tasks_live_updates.html")
        self.assertContains(response, f"task-row-{creation.task.id}")
        self.assertTrue(response.context["has_active_tasks"])

    def test_notes_status_includes_solution_kind(self):
        NoteRecord.objects.create(
            problem=self.p1,
            kind=NoteRecord.Kind.SOLUTION,
            output_dir="/dummy",
            relative_path="solution.md",
            content_hash="abc",
            written_at=timezone.now(),
        )
        url = reverse("ui:problems")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        p1_obj = next(p for p in response.context["page_obj"] if p.id == self.p1.id)
        self.assertTrue(p1_obj.has_note)

    def test_cancelled_task_detail_with_valid_ai_draft(self):
        from prefill.models import AIResponse
        from pipeline.tasks.creation import create_task

        AIResponse.objects.create(
            problem=self.p1,
            source=AIResponse.Source.MANUAL,
            prompt_version="v1",
            input_hash="hash123",
            raw_text="{}",
            parsed={"summary": "test"},
            status=AIResponse.Status.VALID,
            is_active=True,
        )
        creation = create_task([self.p1], overrides={"provider": "manual"})
        task = creation.task
        task.status = Task.Status.CANCELLED
        task.save(update_fields=["status"])

        url = reverse("ui:task_detail", kwargs={"task_id": task.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["status_label"], "Cancelled")
        self.assertContains(response, "Task was cancelled")

    def test_cancelled_task_in_tasks_list_shows_cancelled_not_waiting(self):
        from pipeline.tasks.creation import create_task

        creation = create_task([self.p1], overrides={"provider": "manual"})
        task = creation.task
        task.items.update(status=TaskItem.Status.NEEDS_INPUT, input_kind=TaskItem.InputKind.MANUAL_PREFILL)
        task.status = Task.Status.CANCELLED
        task.save(update_fields=["status"])

        url = reverse("ui:tasks_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        task_data = next(d for d in response.context["tasks_data"] if d["task"].id == task.id)
        self.assertEqual(task_data["status_info"]["status_label"], "Cancelled")
        self.assertFalse(task_data["is_active"])
        self.assertContains(response, "Cancelled")
        self.assertNotContains(response, "Waiting for input")
