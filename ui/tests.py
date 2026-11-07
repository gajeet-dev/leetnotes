import json
from unittest.mock import patch
from django.test import TestCase, Client
from django.urls import reverse

from problems.models import Problem, SyncTask, Submission


class ProblemsUITests(TestCase):
    def setUp(self):
        self.client = Client()

        # Problem 1: Fully Synced
        self.p_synced = Problem.objects.create(
            leetcode_id=1,
            title="Two Sum",
            slug="two-sum",
            difficulty=Problem.Difficulty.EASY,
            solved=True,
        )
        SyncTask.objects.create(
            problem=self.p_synced,
            part=SyncTask.Part.CONTENT,
            status=SyncTask.Status.DONE,
        )
        SyncTask.objects.create(
            problem=self.p_synced,
            part=SyncTask.Part.SUBMISSIONS,
            status=SyncTask.Status.DONE,
        )

        # Problem 2: Has Pending Part
        self.p_pending = Problem.objects.create(
            leetcode_id=2,
            title="Add Two Numbers",
            slug="add-two-numbers",
            difficulty=Problem.Difficulty.MEDIUM,
            solved=True,
        )
        SyncTask.objects.create(
            problem=self.p_pending,
            part=SyncTask.Part.CONTENT,
            status=SyncTask.Status.DONE,
        )
        SyncTask.objects.create(
            problem=self.p_pending,
            part=SyncTask.Part.SUBMISSIONS,
            status=SyncTask.Status.PENDING,
        )

        # Problem 3: Has Failed Part
        self.p_failed = Problem.objects.create(
            leetcode_id=3,
            title="Longest Substring",
            slug="longest-substring",
            difficulty=Problem.Difficulty.MEDIUM,
            solved=False,
        )
        SyncTask.objects.create(
            problem=self.p_failed,
            part=SyncTask.Part.CONTENT,
            status=SyncTask.Status.FAILED,
            last_error="HTTP 500",
        )

        # Problem 4: No Sync Tasks
        self.p_none = Problem.objects.create(
            leetcode_id=4,
            title="Median of Two Sorted Arrays",
            slug="median-of-two-arrays",
            difficulty=Problem.Difficulty.HARD,
            paid_only=True,
            solved=False,
        )

    def test_problems_list_page_renders(self):
        url = reverse("ui:problems")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "ui/problems.html")
        self.assertTemplateUsed(response, "ui/partials/problems_table.html")
        self.assertContains(response, "selection-options-bar")
        self.assertContains(response, "Sync Selected")
        self.assertContains(response, "Synced")
        self.assertContains(response, "Pending (1/2)")
        self.assertContains(response, "Failed (1)")
        self.assertContains(response, "N/A")
        # Ensure hx-push-url and autocomplete="off" are enabled for URL sync on reload
        self.assertContains(response, 'hx-push-url="true"')
        self.assertContains(response, 'autocomplete="off"')

    def test_full_page_reload_with_filter_renders_filtered_data_and_selected_state(self):
        """Simulates browser refresh (full GET, not HX-Request) when filters are in URL."""
        url = reverse("ui:problems")
        response = self.client.get(url, {"sync": "pending", "difficulty": "Medium"})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "ui/problems.html")
        self.assertTemplateUsed(response, "ui/partials/problems_table.html")

        # The table should strictly contain the filtered problem
        object_ids = [p.id for p in response.context["page_obj"]]
        self.assertEqual(object_ids, [self.p_pending.id])

        # The HTML should have selected state in hidden inputs and active pills
        html = response.content.decode()
        self.assertIn('<input type="hidden" name="sync" value="pending" />', html)
        self.assertIn('<input type="hidden" name="difficulty" value="Medium" />', html)
        self.assertIn('Sync: Needs Sync', html)
        self.assertIn('Difficulty: Medium', html)
        # Ensure unfiltered data like Two Sum (Easy, Synced) is NOT in the filtered rows
        self.assertNotIn("Two Sum", html)

    def test_problems_list_htmx_partial(self):
        url = reverse("ui:problems")
        response = self.client.get(url, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "ui/partials/problems_table.html")
        self.assertTemplateNotUsed(response, "ui/problems.html")

    def test_filter_synced(self):
        url = reverse("ui:problems")
        response = self.client.get(url, {"sync": "synced"})
        self.assertEqual(response.status_code, 200)
        object_ids = [p.id for p in response.context["page_obj"]]
        self.assertIn(self.p_synced.id, object_ids)
        self.assertNotIn(self.p_pending.id, object_ids)
        self.assertNotIn(self.p_failed.id, object_ids)
        self.assertNotIn(self.p_none.id, object_ids)

    def test_filter_pending(self):
        url = reverse("ui:problems")
        response = self.client.get(url, {"sync": "pending"})
        self.assertEqual(response.status_code, 200)
        object_ids = [p.id for p in response.context["page_obj"]]
        self.assertIn(self.p_pending.id, object_ids)
        self.assertNotIn(self.p_synced.id, object_ids)
        self.assertNotIn(self.p_failed.id, object_ids)
        self.assertNotIn(self.p_none.id, object_ids)

    def test_filter_failed(self):
        url = reverse("ui:problems")
        response = self.client.get(url, {"sync": "failed"})
        self.assertEqual(response.status_code, 200)
        object_ids = [p.id for p in response.context["page_obj"]]
        self.assertIn(self.p_failed.id, object_ids)
        self.assertNotIn(self.p_synced.id, object_ids)

    def test_filter_none(self):
        url = reverse("ui:problems")
        response = self.client.get(url, {"sync": "none"})
        self.assertEqual(response.status_code, 200)
        object_ids = [p.id for p in response.context["page_obj"]]
        self.assertIn(self.p_none.id, object_ids)
        self.assertNotIn(self.p_synced.id, object_ids)

    @patch("ui.views.start_background_sync")
    def test_sync_selected_problems_success(self, mock_start):
        mock_start.return_value = True
        url = reverse("ui:sync_selected")
        response = self.client.post(
            url,
            json.dumps({"problem_ids": [self.p_pending.id]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["count"], 1)
        mock_start.assert_called_once_with([self.p_pending.id], retry_failed=True)

    @patch("ui.views.start_background_sync")
    def test_sync_selected_automatically_includes_failed_tasks(self, mock_start):
        mock_start.return_value = True
        url = reverse("ui:sync_selected")
        response = self.client.post(
            url,
            json.dumps({"problem_ids": [self.p_failed.id]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["count"], 1)
        mock_start.assert_called_once_with([self.p_failed.id], retry_failed=True)

    @patch("ui.views.start_background_sync")
    def test_sync_selected_nothing_to_sync(self, mock_start):
        url = reverse("ui:sync_selected")
        response = self.client.post(
            url,
            json.dumps({"problem_ids": [self.p_synced.id]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "info")
        self.assertEqual(data["count"], 0)
        mock_start.assert_not_called()

    @patch("ui.views.start_background_sync")
    def test_sync_selected_already_running(self, mock_start):
        mock_start.return_value = False
        url = reverse("ui:sync_selected")
        response = self.client.post(
            url,
            json.dumps({"problem_ids": [self.p_pending.id], "retry_failed": False}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "warning")
        self.assertIn("already running", data["message"])

    def test_sync_selected_no_selection(self):
        url = reverse("ui:sync_selected")
        response = self.client.post(
            url,
            json.dumps({"problem_ids": []}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_refetch_selected_submissions(self):
        url = reverse("ui:refetch_selected")
        response = self.client.post(
            url,
            json.dumps({"problem_ids": [self.p_synced.id], "part": "submissions"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["tasks_reset"], 1)

        task = SyncTask.objects.get(problem=self.p_synced, part=SyncTask.Part.SUBMISSIONS)
        self.assertEqual(task.status, SyncTask.Status.PENDING)

    def test_refetch_selected_content_cascades(self):
        # Add images task to p_synced to test cascade
        SyncTask.objects.create(
            problem=self.p_synced,
            part=SyncTask.Part.IMAGES,
            status=SyncTask.Status.DONE,
        )
        url = reverse("ui:refetch_selected")
        response = self.client.post(
            url,
            json.dumps({"problem_ids": [self.p_synced.id], "part": "content"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        # Both content and images reset
        self.assertEqual(data["tasks_reset"], 2)

    def test_refetch_selected_invalid_part(self):
        url = reverse("ui:refetch_selected")
        response = self.client.post(
            url,
            json.dumps({"problem_ids": [self.p_synced.id], "part": "invalid"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_filter_panel_renders_on_full_page(self):
        """Full page GET renders the 6-column filter panel without duplicate OOB swap markup."""
        url = reverse("ui:problems")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "ui/partials/filter_panel.html")

        # Check facet counts: 4 problems total (2 solved, 1 pending sync, 1 easy, 2 medium, 1 hard, 1 paid, 3 free)
        facets = response.context["facets"]
        self.assertEqual(facets["solved_count"], 2)
        self.assertEqual(facets["pending_sync_count"], 1)
        self.assertEqual(facets["easy_count"], 1)
        self.assertEqual(facets["medium_count"], 2)
        self.assertEqual(facets["hard_count"], 1)
        self.assertEqual(facets["paid_count"], 1)
        self.assertEqual(facets["free_count"], 3)
        self.assertEqual(facets["total_category"], 4)
        self.assertEqual(facets["total_paid"], 4)
        self.assertEqual(facets["total_difficulty"], 4)
        self.assertEqual(facets["total_status"], 4)

        # On full page load, is_htmx is False so hx-swap-oob should NOT be rendered
        self.assertNotContains(response, 'hx-swap-oob="outerHTML"')

    def test_filter_paid(self):
        url = reverse("ui:problems")
        response = self.client.get(url, {"paid": "paid"})
        self.assertEqual(response.status_code, 200)
        object_ids = [p.id for p in response.context["page_obj"]]
        self.assertEqual(object_ids, [self.p_none.id])

    def test_filter_free(self):
        url = reverse("ui:problems")
        response = self.client.get(url, {"paid": "free"})
        self.assertEqual(response.status_code, 200)
        object_ids = [p.id for p in response.context["page_obj"]]
        self.assertIn(self.p_synced.id, object_ids)
        self.assertIn(self.p_pending.id, object_ids)
        self.assertIn(self.p_failed.id, object_ids)
        self.assertNotIn(self.p_none.id, object_ids)

    def test_htmx_request_includes_oob_swaps_for_filter_panel(self):
        """HTMX requests should return the table along with OOB swap for the filter panel."""
        url = reverse("ui:problems")
        response = self.client.get(url, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "ui/partials/problems_table.html")
        self.assertTemplateUsed(response, "ui/partials/filter_panel.html")

        # Must have out-of-band swap directives for filter-panel and filter-count-badge
        content = response.content.decode("utf-8")
        self.assertIn('id="filter-panel"', content)
        self.assertIn('id="filter-count-badge"', content)
        self.assertIn('hx-swap-oob="outerHTML"', content)

    def test_reactive_faceted_counts_when_status_filter_applied(self):
        """When status=solved is applied, difficulty facets reflect only solved problems."""
        url = reverse("ui:problems")
        response = self.client.get(url, {"status": "solved"}, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)

        facets = response.context["facets"]
        # Solved problems: Two Sum (Easy), Add Two Numbers (Medium). Hard has 0.
        self.assertEqual(facets["easy_count"], 1)
        self.assertEqual(facets["medium_count"], 1)
        self.assertEqual(facets["hard_count"], 0)

        # But status facet itself excludes the status filter so user sees Solved (2) and Unsolved (2)
        self.assertEqual(facets["solved_count"], 2)
        self.assertEqual(facets["unsolved_count"], 2)

    def test_reactive_faceted_counts_when_combined_filters_applied(self):
        """When status=solved AND difficulty=Easy are both active, cross facets react accordingly."""
        url = reverse("ui:problems")
        response = self.client.get(url, {"status": "solved", "difficulty": "Easy"}, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)

        facets = response.context["facets"]
        # For difficulty facet: status=solved is fixed, difficulty is varied. Easy=1, Medium=1, Hard=0.
        self.assertEqual(facets["easy_count"], 1)
        self.assertEqual(facets["medium_count"], 1)
        self.assertEqual(facets["hard_count"], 0)

        # For status facet: difficulty=Easy is fixed, status is varied. Solved=1 (Two Sum), Unsolved=0.
        self.assertEqual(facets["solved_count"], 1)
        self.assertEqual(facets["unsolved_count"], 0)

    def test_category_all_count_includes_paid_problems(self):
        """Category 'All' count must include paid questions even if they have category=None, and 'None' is listed."""
        # Set a category on one free problem
        self.p_synced.category = Problem.Category.ALGORITHMS
        self.p_synced.save(update_fields=["category"])

        url = reverse("ui:problems")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        facets = response.context["facets"]
        # Total category 'All' should be all 4 problems (3 free + 1 paid)
        self.assertEqual(facets["total_category"], 4)
        self.assertEqual(facets["category_counts"]["Algorithms"], 1)
        # The remaining 3 problems have category=None
        self.assertEqual(facets["category_counts"]["None"], 3)
        self.assertEqual(sum(facets["category_counts"].values()), 4)
        self.assertEqual(facets["paid_count"], 1)

    def test_filter_category_none(self):
        """Filtering by category=None returns only problems where category is null or empty."""
        self.p_synced.category = Problem.Category.ALGORITHMS
        self.p_synced.save(update_fields=["category"])

        url = reverse("ui:problems")
        response = self.client.get(url, {"category": "None"})
        self.assertEqual(response.status_code, 200)

        object_ids = [p.id for p in response.context["page_obj"]]
        self.assertNotIn(self.p_synced.id, object_ids)
        self.assertIn(self.p_pending.id, object_ids)
        self.assertIn(self.p_failed.id, object_ids)
        self.assertIn(self.p_none.id, object_ids)

    def test_matching_ids_all(self):
        url = reverse("ui:matching_ids")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["count"], 4)
        self.assertEqual(len(data["ids"]), 4)
        self.assertIn(self.p_synced.id, data["ids"])

    def test_matching_ids_filtered_by_status(self):
        url = reverse("ui:matching_ids")
        response = self.client.get(url, {"status": "solved"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["count"], 2)
        self.assertIn(self.p_synced.id, data["ids"])
        self.assertIn(self.p_pending.id, data["ids"])
        self.assertNotIn(self.p_failed.id, data["ids"])

    def test_matching_ids_filtered_by_paid(self):
        url = reverse("ui:matching_ids")
        response = self.client.get(url, {"paid": "paid"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["ids"], [self.p_none.id])

    def test_select_all_ui_elements_rendered(self):
        url = reverse("ui:problems")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # Select all button and badge exist in the options bar
        self.assertContains(response, 'id="btn-select-all-matching"')
        self.assertContains(response, 'id="all-matching-selected-badge"')
        self.assertContains(response, 'data-total-count="4"')
        # Duplicate table header banner is not rendered
        self.assertNotContains(response, 'id="select-all-page-banner"')
