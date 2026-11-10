import json
import tempfile
from pathlib import Path
from unittest import mock
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse

from problems.models import Problem, SyncTask
from userconfig import effective, update_config, update_credentials
from userconfig.credentials import load_credentials
from userconfig.paths import PROJECT_ROOT
from userconfig.store import load_config
from userconfig.tests.helpers import DataDirDBTestCase


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

    def test_full_page_reload_with_filter_renders_filtered_data_and_selected_state(
        self,
    ):
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
        self.assertIn("Sync: Needs Sync", html)
        self.assertIn("Difficulty: Medium", html)
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

    @patch("ui.views.start_background_sync")
    def test_refetch_selected_submissions(self, mock_start):
        mock_start.return_value = True
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
        self.assertTrue(data["sync_started"])
        mock_start.assert_called_once_with([self.p_synced.id], retry_failed=True)

        task = SyncTask.objects.get(
            problem=self.p_synced, part=SyncTask.Part.SUBMISSIONS
        )
        self.assertEqual(task.status, SyncTask.Status.PENDING)

    @patch("ui.views.start_background_sync")
    def test_refetch_selected_content_cascades(self, mock_start):
        mock_start.return_value = True
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
        self.assertTrue(data["sync_started"])
        mock_start.assert_called_once_with([self.p_synced.id], retry_failed=True)

    @patch("ui.views.start_background_sync")
    def test_refetch_selected_content_images_cascades(self, mock_start):
        mock_start.return_value = True
        SyncTask.objects.create(
            problem=self.p_synced,
            part=SyncTask.Part.IMAGES,
            status=SyncTask.Status.DONE,
        )
        url = reverse("ui:refetch_selected")
        response = self.client.post(
            url,
            json.dumps({"problem_ids": [self.p_synced.id], "part": "content_images"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["tasks_reset"], 2)
        self.assertTrue(data["sync_started"])
        mock_start.assert_called_once_with([self.p_synced.id], retry_failed=True)

    @patch("ui.views.start_background_sync")
    def test_refetch_selected_all(self, mock_start):
        mock_start.return_value = True
        SyncTask.objects.create(
            problem=self.p_synced,
            part=SyncTask.Part.IMAGES,
            status=SyncTask.Status.DONE,
        )
        url = reverse("ui:refetch_selected")
        response = self.client.post(
            url,
            json.dumps({"problem_ids": [self.p_synced.id], "part": "all"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        # Content cascade (content + images) + submissions = 3 tasks reset
        self.assertEqual(data["tasks_reset"], 3)
        self.assertTrue(data["sync_started"])
        mock_start.assert_called_once_with([self.p_synced.id], retry_failed=True)

    @patch("ui.views.start_background_sync")
    def test_refetch_selected_already_running(self, mock_start):
        mock_start.return_value = False
        url = reverse("ui:refetch_selected")
        response = self.client.post(
            url,
            json.dumps({"problem_ids": [self.p_synced.id], "part": "submissions"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "warning")
        self.assertFalse(data["sync_started"])
        mock_start.assert_called_once_with([self.p_synced.id], retry_failed=True)

    def test_refetch_selected_invalid_part(self):
        url = reverse("ui:refetch_selected")
        response = self.client.post(
            url,
            json.dumps({"problem_ids": [self.p_synced.id], "part": "invalid"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_custom_per_page_pagination(self):
        url = reverse("ui:problems")
        response = self.client.get(url, {"per_page": "1"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["per_page"], 1)
        self.assertEqual(len(response.context["page_obj"]), 1)
        self.assertContains(response, 'name="per_page"')
        self.assertContains(response, 'id="per-page-input-top"')
        self.assertContains(response, 'id="per-page-input-bottom"')

    def test_dual_pagination_top_and_bottom(self):
        url = reverse("ui:problems")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        # Both top and bottom pagination controls are present
        self.assertEqual(content.count("Previous"), 2)
        self.assertEqual(content.count("Next"), 2)
        self.assertIn('id="per-page-input-top"', content)
        self.assertIn('id="per-page-input-bottom"', content)

    def test_refetch_menu_markup(self):
        url = reverse("ui:problems")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Refetch & Sync Content & Images", content)
        self.assertIn("Refetch & Sync All", content)
        # Should only have one Refetch & Sync Submissions button in the dropdown
        self.assertEqual(content.count("Refetch & Sync Submissions"), 1)
        # Should not contain "Refetch Images Only"
        self.assertNotIn("Refetch Images Only", content)
        # Centered confirmation modal is present
        self.assertIn('id="refetch-confirm-modal"', content)

    def test_htmx_filter_reset_button_oob_swap(self):
        url = reverse("ui:problems")
        # HTMX request without active filters: reset container is empty
        response_clean = self.client.get(url, HTTP_HX_REQUEST="true")
        self.assertEqual(response_clean.status_code, 200)
        content_clean = response_clean.content.decode()
        self.assertIn('id="filter-reset-container"', content_clean)
        self.assertNotIn("Reset all filters", content_clean)

        # HTMX request with active filter: reset container includes Reset button
        response_filtered = self.client.get(
            url, {"difficulty": "Easy"}, HTTP_HX_REQUEST="true"
        )
        self.assertEqual(response_filtered.status_code, 200)
        content_filtered = response_filtered.content.decode()
        self.assertIn('id="filter-reset-container"', content_filtered)
        self.assertIn("Reset all filters", content_filtered)

    def test_problem_status_column_only_solved_unsolved(self):
        url = reverse("ui:problems")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("Solved", content)
        self.assertIn("Unsolved", content)
        self.assertNotIn("In Task", content)

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
        response = self.client.get(
            url, {"status": "solved", "difficulty": "Easy"}, HTTP_HX_REQUEST="true"
        )
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


class SettingsUITests(DataDirDBTestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()

    def test_settings_page_get(self):
        url = reverse("ui:settings")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Settings")
        self.assertContains(response, "LeetCode Credentials")
        self.assertContains(response, "Storage & Timezone")
        self.assertContains(response, "Sync & AI Preferences")
        self.assertContains(response, 'id="toast-container"')

    @patch("ui.helpers.settings.check_connection")
    def test_save_credentials_and_auto_sets_username(self, mock_conn):
        mock_conn.return_value = mock.MagicMock(
            ok=True,
            state=mock.MagicMock(value="connected"),
            username="auto_detected_coder",
            is_premium=True,
            message="Connected as auto_detected_coder.",
        )
        url = reverse("ui:save_credentials")
        response = self.client.post(
            url,
            {
                "session": "valid_session_token_12345",
                "csrf_token": "valid_csrf_token_67890",
            },
        )
        self.assertEqual(response.status_code, 200)
        creds = load_credentials()
        self.assertEqual(creds.session, "valid_session_token_12345")
        self.assertEqual(creds.csrf_token, "valid_csrf_token_67890")
        config = load_config()
        self.assertEqual(config.leetcode_username, "auto_detected_coder")

    @patch("ui.helpers.settings.check_tokens_connection")
    def test_save_invalid_credentials_preserves_old_working_credentials(
        self, mock_check
    ):
        update_credentials(
            session="original_good_session", csrf_token="original_good_csrf"
        )

        def side_effect(session, csrf):
            if session == "mistaken_bad_session":
                return mock.MagicMock(
                    ok=False,
                    state=mock.MagicMock(value="signed_out"),
                    message="LeetCode doesn't accept these cookies: they have probably expired.",
                )
            return mock.MagicMock(
                ok=True,
                state=mock.MagicMock(value="connected"),
                username="safe_coder",
                is_premium=False,
                message="Connected as safe_coder.",
            )

        mock_check.side_effect = side_effect

        url = reverse("ui:save_credentials")
        response = self.client.post(
            url,
            {"session": "mistaken_bad_session", "csrf_token": "original_good_csrf"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn(
            "Your previous working credentials for safe_coder were kept", content
        )
        self.assertIn("toast-notification", content)
        self.assertIn('data-persistent="true"', content)
        self.assertEqual(
            response.headers.get("HX-Trigger"),
            json.dumps({"clearCredentialsInputs": True}),
        )

        # Secrets file must still have original good session
        creds = load_credentials()
        self.assertEqual(creds.session, "original_good_session")

    @patch("ui.helpers.settings.check_tokens_connection")
    def test_save_invalid_credentials_when_old_were_expired_saves_new(self, mock_check):
        update_credentials(session="expired_old_session", csrf_token="expired_old_csrf")

        # Both new and old fail connection
        mock_check.return_value = mock.MagicMock(
            ok=False,
            state=mock.MagicMock(value="signed_out"),
            message="Cookies rejected.",
        )

        url = reverse("ui:save_credentials")
        response = self.client.post(
            url,
            {"session": "new_attempt_session", "csrf_token": "new_attempt_csrf"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("Connection Failed", content)
        self.assertIn("Cookies rejected", content)

        # Since old was also invalid, the new attempt is stored
        creds = load_credentials()
        self.assertEqual(creds.session, "new_attempt_session")
        self.assertEqual(creds.csrf_token, "new_attempt_csrf")

    def test_settings_page_auto_checks_connection_when_configured(self):
        update_credentials(session="configured_session", csrf_token="configured_csrf")
        url = reverse("ui:settings")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'hx-trigger="load"')
        self.assertContains(response, reverse("ui:test_connection"))
        self.assertContains(response, "Checking connection...")

    @patch("ui.helpers.settings.check_connection")
    def test_connection_failed_renders_full_message_without_truncate(self, mock_conn):
        full_error = "LeetCode doesn't accept these cookies: they have probably expired. Copy fresh ones from your browser and update them in Settings."
        mock_conn.return_value = mock.MagicMock(
            ok=False,
            state=mock.MagicMock(value="signed_out"),
            message=full_error,
        )
        url = reverse("ui:test_connection")
        response = self.client.post(url, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("Connection Failed", content)
        self.assertIn(
            "Copy fresh ones from your browser and update them in Settings.", content
        )
        self.assertIn("break-words", content)

    @patch("ui.helpers.settings.check_connection")
    def test_test_connection_endpoint(self, mock_conn):
        mock_conn.return_value = mock.MagicMock(
            ok=True,
            state=mock.MagicMock(value="connected"),
            username="test_user",
            is_premium=False,
            message="Connected as test_user.",
        )
        url = reverse("ui:test_connection")
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(
            response.content,
            {
                "ok": True,
                "state": "connected",
                "message": "Connected as test_user.",
                "username": "test_user",
                "is_premium": False,
            },
        )

    def test_save_general_settings(self):
        url = reverse("ui:save_general_settings")
        response = self.client.post(
            url,
            {
                "output_dir": "/tmp/my_vault",
                "time_zone": "America/New_York",
                "problems_per_minute": "10.0",
                "review_before_writing": "true",
            },
        )
        self.assertEqual(response.status_code, 200)
        config = load_config()
        self.assertEqual(config.output_dirs, ["/tmp/my_vault"])
        self.assertEqual(config.time_zone, "America/New_York")
        self.assertEqual(config.problems_per_minute, 10.0)
        self.assertTrue(config.review_before_writing)

    def test_auto_set_timezone_once(self):
        url = reverse("ui:set_timezone")
        response = self.client.post(
            url,
            json.dumps({"timezone": "Asia/Kolkata"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(load_config().time_zone, "Asia/Kolkata")

        # Second call does not overwrite already saved timezone
        response2 = self.client.post(
            url,
            json.dumps({"timezone": "Europe/London"}),
            content_type="application/json",
        )
        self.assertEqual(response2.status_code, 200)
        self.assertEqual(load_config().time_zone, "Asia/Kolkata")

    def test_save_existing_directory_shows_success_and_exists_badge(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            url = reverse("ui:save_general_settings")
            response = self.client.post(
                url,
                {"output_dir": tmp_dir},
                HTTP_HX_REQUEST="true",
            )
            self.assertEqual(response.status_code, 200)
            content = response.content.decode("utf-8")
            self.assertIn("Settings saved successfully.", content)
            self.assertIn("toast-notification", content)
            self.assertIn("Directory exists on disk", content)
            self.assertIn('id="output-dir-status-badge"', content)
            self.assertIn('hx-swap-oob="outerHTML"', content)

    def test_save_missing_directory_without_toggle_shows_warning_badge(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            missing_dir = Path(tmp_dir) / "does_not_exist"
            url = reverse("ui:save_general_settings")
            response = self.client.post(
                url,
                {"output_dir": str(missing_dir)},
                HTTP_HX_REQUEST="true",
            )
            self.assertEqual(response.status_code, 200)
            content = response.content.decode("utf-8")
            self.assertIn("does not exist on disk", content)
            self.assertIn("Directory does not exist", content)
            self.assertIn('id="output-dir-status-badge"', content)
            self.assertIn('hx-swap-oob="outerHTML"', content)
            self.assertFalse(missing_dir.exists())

    def test_save_missing_directory_with_create_toggle_creates_directory(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            new_dir = Path(tmp_dir) / "subfolder" / "notes_vault"
            self.assertFalse(new_dir.exists())
            url = reverse("ui:save_general_settings")
            response = self.client.post(
                url,
                {"output_dir": str(new_dir), "create_directory": "true"},
                HTTP_HX_REQUEST="true",
            )
            self.assertEqual(response.status_code, 200)
            content = response.content.decode("utf-8")
            self.assertIn("Created directory", content)
            self.assertIn("Directory exists on disk", content)
            self.assertTrue(new_dir.is_dir())
            config = load_config()
            self.assertTrue(config.create_directory)

    def test_reset_settings_restores_defaults_and_preserves_secrets(self):
        # 1. Setup custom settings and credentials
        update_credentials(session="my-secret-session", csrf_token="my-csrf-token")
        update_config(
            {
                "output_dirs": ["/custom/vault"],
                "create_directory": True,
                "leetcode_username": "my_account",
                "time_zone": "Europe/London",
                "problems_per_minute": 15.0,
                "default_provider": "claude",
                "review_before_writing": False,
            }
        )

        # Verify custom settings are present
        cfg_before = load_config()
        self.assertEqual(cfg_before.output_dirs, ["/custom/vault"])
        self.assertEqual(cfg_before.problems_per_minute, 15.0)
        self.assertEqual(cfg_before.default_provider, "claude")

        # 2. Call reset_settings endpoint with HTMX
        url = reverse("ui:reset_settings")
        response = self.client.post(url, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)

        # 3. Assert config in data/config.json is reset to defaults
        cfg_after = load_config()
        self.assertIsNone(
            cfg_after.output_dirs
        )  # None in config.json means default notes_output
        self.assertIsNone(cfg_after.time_zone)
        self.assertIsNone(cfg_after.problems_per_minute)
        self.assertIsNone(cfg_after.default_provider)
        self.assertFalse(cfg_after.create_directory)
        self.assertTrue(cfg_after.review_before_writing)
        # Username linked to login is preserved
        self.assertEqual(cfg_after.leetcode_username, "my_account")

        # Effective values reflect defaults
        self.assertEqual(effective.problems_per_minute(), 5.0)
        self.assertEqual(effective.output_dirs(), [PROJECT_ROOT / "notes_output"])

        # 4. CRITICAL: Assert credentials in secrets.json were completely preserved!
        creds_after = load_credentials()
        self.assertEqual(creds_after.session, "my-secret-session")
        self.assertEqual(creds_after.csrf_token, "my-csrf-token")
        self.assertEqual(effective.leetcode_session(), "my-secret-session")
        self.assertEqual(effective.leetcode_csrf_token(), "my-csrf-token")

        # 5. Check response content includes toast and configurable-settings swap
        content = response.content.decode("utf-8")
        self.assertIn("Preferences reset to default values", content)
        self.assertIn("Saved credentials preserved", content)
        self.assertIn('id="configurable-settings"', content)
        self.assertIn('hx-swap-oob="outerHTML:#configurable-settings"', content)

    def test_settings_page_renders_alert_dialog_and_basecoat_dropdowns(self):
        url = reverse("ui:settings")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")

        # 1. Alert dialog component for reset confirmation
        self.assertIn('id="reset-alert-dialog"', content)
        self.assertIn('class="alert-dialog"', content)
        self.assertIn("Reset to Defaults?", content)
        self.assertIn("Cancel", content)
        self.assertNotIn("hx-confirm=", content)

        # 2. No default HTML select tags
        self.assertNotIn("<select", content)

        # 3. Fetch problems/minute label and non-empty default of 5
        self.assertIn("Fetch problems/minute", content)
        self.assertIn('name="problems_per_minute"', content)
        self.assertIn('value="5"', content)

        # 4. Timezone dropdown with explicit UTC option
        self.assertIn('id="timezone-dropdown"', content)
        self.assertIn('data-tz="UTC"', content)
        self.assertIn("UTC (Coordinated Universal Time)", content)

        # 5. Provider dropdown with manual as default
        self.assertIn('id="provider-dropdown"', content)
        self.assertIn('data-provider="manual"', content)
        self.assertIn("manual (paste reply in browser)", content)

    def test_independent_form_saves_do_not_override_other_preferences(self):
        # Set initial preferences
        update_config(
            {
                "output_dirs": ["/tmp/initial_vault"],
                "create_directory": True,
                "problems_per_minute": 10.0,
                "review_before_writing": False,
            }
        )

        url = reverse("ui:save_general_settings")

        # Save Card 2 (Storage & Timezone) only - does not pass problems_per_minute or review_before_writing
        response2 = self.client.post(
            url,
            {
                "output_dir": "/tmp/new_test_vault",
                "create_directory": "true",
                "time_zone": "Europe/London",
            },
        )
        self.assertEqual(response2.status_code, 200)
        cfg2 = load_config()
        self.assertEqual(cfg2.output_dirs, ["/tmp/new_test_vault"])
        self.assertEqual(cfg2.time_zone, "Europe/London")
        self.assertTrue(cfg2.create_directory)
        # Card 3 preferences are preserved!
        self.assertEqual(cfg2.problems_per_minute, 10.0)
        self.assertFalse(cfg2.review_before_writing)

        # Save Card 3 (Sync & AI) only - does not pass output_dir or create_directory
        response3 = self.client.post(
            url,
            {
                "problems_per_minute": "12.0",
                "default_provider": "claude",
                "review_before_writing": "true",
            },
        )
        self.assertEqual(response3.status_code, 200)
        cfg3 = load_config()
        self.assertEqual(cfg3.problems_per_minute, 12.0)
        self.assertEqual(cfg3.default_provider, "claude")
        self.assertTrue(cfg3.review_before_writing)
        # Card 2 preferences are preserved!
        self.assertEqual(cfg3.output_dirs, ["/tmp/new_test_vault"])
        self.assertEqual(cfg3.time_zone, "Europe/London")
        self.assertTrue(cfg3.create_directory)

    def test_save_provider_models_and_efforts(self):
        url = reverse("ui:save_general_settings")
        response = self.client.post(
            url,
            {
                "default_provider": "agy",
                "claude_model_choice": "opus",
                "claude_effort": "high",
                "agy_model_choice": "custom",
                "agy_model_custom": "gemini-3.8-flash-high",
                "agy_effort": "max",
            },
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        cfg = load_config()
        self.assertEqual(cfg.default_provider, "agy")
        self.assertIn("claude", cfg.providers)
        self.assertEqual(cfg.providers["claude"]["model"], "opus")
        self.assertEqual(cfg.providers["claude"]["effort"], "high")
        self.assertIn("agy", cfg.providers)
        self.assertEqual(cfg.providers["agy"]["model"], "gemini-3.8-flash-high")
        self.assertEqual(cfg.providers["agy"]["effort"], "max")

    def test_refresh_agy_models_endpoint(self):
        url = reverse("ui:refresh_agy_models")
        with patch("ui.views.get_agy_models", return_value=[{"id": "test-m", "name": "Test Model"}]):
            response = self.client.post(url, HTTP_HX_REQUEST="true")
            self.assertEqual(response.status_code, 200)
            content = response.content.decode("utf-8")
            self.assertIn("Retrieved 1 models from Antigravity CLI", content)
            self.assertIn("test-m", content)

    def test_settings_context_defaults_effort_to_medium(self):
        from ui.helpers.settings import get_settings_context
        ctx = get_settings_context()
        self.assertEqual(ctx["claude_effort"], "medium")
        self.assertEqual(ctx["agy_effort"], "medium")
