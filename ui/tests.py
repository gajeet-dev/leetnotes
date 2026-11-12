import json
import tempfile
from pathlib import Path
from unittest import mock
from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from notes.models import NoteRecord
from prefill.models import AIResponse
from problems.models import Problem, Submission, SyncTask
from userconfig import effective, update_config, update_credentials
from userconfig.credentials import load_credentials
from userconfig.paths import PROJECT_ROOT
from userconfig.store import load_config
from userconfig.tests.helpers import DataDirDBTestCase


class ProblemsUITests(TestCase):
    def setUp(self):
        cache.clear()
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

    def test_dashboard_page_renders(self):
        """Verify the dashboard page skeleton, health bar, KPI cards, and generation section."""
        for url_name in ("ui:dashboard", "ui:dashboard_alias"):
            url = reverse(url_name)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, "ui/dashboard.html")
            self.assertEqual(response.context["active"], "dashboard")

            # Check header
            self.assertContains(response, "Dashboard")
            self.assertContains(response, "Your leetcode notes workspace")

            # Section 1: Health bar (stacked individual lines)
            self.assertTemplateUsed(response, "ui/partials/dashboard_connection_status.html")
            self.assertContains(response, "LeetCode Connection")
            self.assertContains(response, 'id="dashboard-leetcode-status"')
            self.assertContains(response, 'hx-trigger="load"')
            self.assertContains(response, "Checking connection...")
            self.assertContains(response, "AI Provider")
            self.assertContains(response, "Output Directory")
            self.assertContains(response, 'id="vault-path-text"')
            self.assertContains(response, 'id="vault-path-inline"')
            self.assertContains(response, 'id="vault-path-expanded"')
            self.assertContains(response, 'id="vault-path-toggle-btn"')
            self.assertContains(response, "view full")

            # Section 2: Library & KPI cards
            self.assertTemplateUsed(response, "ui/partials/dashboard_kpi_solved.html")
            self.assertTemplateUsed(response, "ui/partials/dashboard_kpi_notes.html")
            self.assertTemplateUsed(response, "ui/partials/dashboard_kpi_prefills.html")
            self.assertTemplateUsed(response, "ui/partials/dashboard_kpi_attention.html")
            self.assertContains(response, "Your notes library")
            self.assertContains(response, "An overview of you progress")
            self.assertContains(response, "Sync Leetcode Data")
            self.assertContains(response, 'id="sync-stats-container"')
            self.assertContains(response, "Problems Solved")
            self.assertContains(response, 'id="kpi-solved-problems"')
            self.assertContains(response, "Difficulty Breakdown")
            self.assertContains(response, "Notes Created")
            self.assertContains(response, 'id="kpi-notes-created"')
            self.assertContains(response, "Vault Coverage")
            self.assertContains(response, "AI Prefills Generated")
            self.assertContains(response, 'id="kpi-ai-prefills"')
            self.assertContains(response, "Needs Attention")
            self.assertContains(response, 'id="kpi-needs-attention"')

            # Section 3: Notes generation section
            self.assertContains(response, "Notes Generation")
            self.assertContains(response, "Generate Notes")
            self.assertContains(response, "Overall Running Tasks")

    def test_dashboard_ai_provider_display_manual(self):
        """When manual is selected, show 'Manual input AI prefill' with no effort badge."""
        update_config({"default_provider": "manual"})
        response = self.client.get(reverse("ui:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Manual input AI prefill")
        self.assertNotContains(response, "effort [")

    def test_dashboard_ai_provider_display_claude(self):
        """When claude is selected, show model and effort."""
        update_config({
            "default_provider": "claude",
            "providers": {
                "claude": {
                    "model": "sonnet",
                    "effort": "high",
                }
            }
        })
        response = self.client.get(reverse("ui:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Claude Code · Claude Sonnet")
        self.assertContains(response, "effort [high]")

    def test_dashboard_ai_provider_display_agy(self):
        """When agy is selected, show model and effort."""
        update_config({
            "default_provider": "agy",
            "providers": {
                "agy": {
                    "model": "antigravity-3.8-thinking",
                    "effort": "medium",
                }
            }
        })
        response = self.client.get(reverse("ui:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Antigravity CLI (agy) · antigravity-3.8-thinking")
        self.assertContains(response, "effort [medium]")

    def test_dashboard_ai_provider_display_agy_default(self):
        """When agy is selected without explicit model, show Default (CLI default)."""
        update_config({
            "default_provider": "agy",
            "providers": {
                "agy": {
                    "model": "",
                    "effort": "low",
                }
            }
        })
        response = self.client.get(reverse("ui:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Antigravity CLI (agy) · Default (CLI default)")
        self.assertContains(response, "effort [low]")

    def test_dashboard_output_dir_display_exists(self):
        """When output directory exists on disk, show 'Ready' badge and path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            update_config({"output_dirs": [tmpdir]})
            try:
                response = self.client.get(reverse("ui:dashboard"))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, tmpdir)
                self.assertContains(response, "Ready")
                self.assertContains(response, "view full")
            finally:
                update_config({"output_dirs": None})

    def test_dashboard_output_dir_display_missing(self):
        """When output directory does not exist on disk, show 'Missing' badge and path."""
        missing_path = "/nonexistent/test/path/leetnotes_vault"
        update_config({"output_dirs": [missing_path]})
        try:
            response = self.client.get(reverse("ui:dashboard"))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, missing_path)
            self.assertContains(response, "Missing")
            self.assertContains(response, "view full")
        finally:
            update_config({"output_dirs": None})

    @patch("userconfig.effective.output_dirs", return_value=[])
    def test_dashboard_output_dir_display_not_configured(self, mock_dirs):
        """When output directory is empty, show 'Not Configured' badge and fallback text."""
        response = self.client.get(reverse("ui:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Not Configured")
        self.assertContains(response, "No directory configured yet")

    @patch("ui.views.effective.leetcode_session", return_value="")
    @patch("ui.views.effective.leetcode_csrf_token", return_value="")
    def test_sync_leetcode_data_view_unauthenticated(self, mock_csrf, mock_session):
        url = reverse("ui:sync_leetcode_data")
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "ui/partials/sync_leetcode_button.html")
        self.assertContains(response, "LeetCode credentials not configured")
        self.assertContains(response, 'id="toast-container"')

    @patch("ui.views.effective.leetcode_session", return_value="fake_session")
    @patch("ui.views.effective.leetcode_csrf_token", return_value="fake_csrf")
    @patch("ui.views.get_client")
    @patch("ui.views.sync_leetcode_overview")
    def test_sync_leetcode_data_view_success(self, mock_sync, mock_client, mock_csrf, mock_session):
        from problems.services.sync import SyncOverviewResult
        mock_sync.return_value = SyncOverviewResult(
            total_catalog=100,
            new_problems=3,
            newly_solved=1,
            stale_detected=2,
            pinned_with_updates=1,
        )
        url = reverse("ui:sync_leetcode_data")
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "ui/partials/sync_leetcode_button.html")
        self.assertTemplateUsed(response, "ui/partials/sync_stats_strip.html")
        self.assertTemplateUsed(response, "ui/partials/dashboard_kpi_solved.html")
        self.assertTemplateUsed(response, "ui/partials/dashboard_kpi_notes.html")
        self.assertTemplateUsed(response, "ui/partials/dashboard_kpi_attention.html")

        # Verify inline message strip & pills
        self.assertContains(response, 'id="sync-stats-container" hx-swap-oob="innerHTML"')
        self.assertContains(response, 'id="sync-stats-strip"')
        self.assertContains(response, "+3")
        self.assertContains(response, "new discovered")
        self.assertContains(response, "+1")
        self.assertContains(response, "newly solved")
        self.assertContains(response, "2")
        self.assertContains(response, "stale submissions")
        self.assertContains(response, "100")
        self.assertContains(response, "in catalog")

        # Verify OOB KPI updates
        self.assertContains(response, 'id="kpi-solved-problems" hx-swap-oob="outerHTML"')
        self.assertContains(response, 'id="kpi-notes-created" hx-swap-oob="outerHTML"')
        self.assertContains(response, 'id="kpi-needs-attention" hx-swap-oob="outerHTML"')

        # Verify NO toast notification is included for success
        self.assertNotContains(response, 'id="toast-container"')
        self.assertNotContains(response, "toast-notification")

    def test_dashboard_kpi_metrics_helpers(self):
        """Test calculation of live metrics in ui.helpers.dashboard."""
        from ui.helpers.dashboard import (
            get_dashboard_metrics,
            get_needs_attention_metrics,
            get_solved_problems_metrics,
        )

        # Clear existing problems & create known set
        Problem.objects.all().delete()
        Problem.objects.create(
            slug="two-sum",
            leetcode_id=1,
            difficulty=Problem.Difficulty.EASY,
            solved=True,
        )
        Problem.objects.create(
            slug="add-two-numbers",
            leetcode_id=2,
            difficulty=Problem.Difficulty.MEDIUM,
            solved=True,
        )
        p3 = Problem.objects.create(
            slug="longest-substring",
            leetcode_id=3,
            difficulty=Problem.Difficulty.MEDIUM,
            solved=False,
        )
        Problem.objects.create(
            slug="median-arrays",
            leetcode_id=4,
            difficulty=Problem.Difficulty.HARD,
            solved=False,
        )

        solved_metrics = get_solved_problems_metrics()
        self.assertEqual(solved_metrics["total_problems"], 4)
        self.assertEqual(solved_metrics["total_solved"], 2)
        self.assertEqual(solved_metrics["overall_progress_pct"], 50.0)
        self.assertEqual(solved_metrics["easy"]["solved"], 1)
        self.assertEqual(solved_metrics["easy"]["total"], 1)
        self.assertEqual(solved_metrics["easy"]["pct"], 100.0)
        self.assertEqual(solved_metrics["medium"]["solved"], 1)
        self.assertEqual(solved_metrics["medium"]["total"], 2)
        self.assertEqual(solved_metrics["medium"]["pct"], 50.0)
        self.assertEqual(solved_metrics["hard"]["solved"], 0)
        self.assertEqual(solved_metrics["hard"]["total"], 1)
        self.assertEqual(solved_metrics["hard"]["pct"], 0.0)

        # Needs attention with no stale submissions
        attention = get_needs_attention_metrics()
        self.assertEqual(attention["total"], 0)
        self.assertFalse(attention["has_items"])
        self.assertEqual(len(attention["categories"]), 0)

        # Create stale submissions: 1 unpinned, 1 pinned
        now = timezone.now()
        Submission.objects.create(
            problem=p3,
            submission_id=1001,
            lang="python3",
            code="pass",
            submission_date=now,
            is_stale=True,
            pinned=False,
        )
        p2 = Problem.objects.get(slug="add-two-numbers")
        Submission.objects.create(
            problem=p2,
            submission_id=1002,
            lang="python3",
            code="pass",
            submission_date=now,
            is_stale=True,
            pinned=True,
        )

        attention = get_needs_attention_metrics()
        self.assertEqual(attention["total"], 1)
        self.assertTrue(attention["has_items"])
        self.assertEqual(attention["pinned_with_updates"], 1)
        self.assertEqual(len(attention["categories"]), 1)
        self.assertEqual(attention["categories"][0]["id"], "stale_submissions")
        self.assertEqual(attention["categories"][0]["count"], 1)

        # Test aggregated dashboard metrics
        metrics = get_dashboard_metrics()
        self.assertEqual(metrics["solved"]["total_solved"], 2)
        self.assertEqual(metrics["attention"]["total"], 1)

    def test_dashboard_notes_created_and_prefills_helpers(self):
        """Test calculation of Notes Created (with disk inspection) and AI Prefills in dashboard helpers."""
        from notes.hashing import content_hash
        from notes.models import NoteRecord
        from notes.state import dir_key
        from prefill.models import AIResponse
        from ui.helpers.dashboard import (
            get_ai_prefills_metrics,
            get_dashboard_metrics,
            get_notes_created_metrics,
        )

        # Clear existing models
        NoteRecord.objects.all().delete()
        AIResponse.objects.all().delete()
        Problem.objects.all().delete()

        p1 = Problem.objects.create(
            slug="two-sum",
            leetcode_id=1,
            difficulty=Problem.Difficulty.EASY,
            solved=True,
        )
        p2 = Problem.objects.create(
            slug="add-two-numbers",
            leetcode_id=2,
            difficulty=Problem.Difficulty.MEDIUM,
            solved=True,
        )
        p3 = Problem.objects.create(
            slug="longest-substring",
            leetcode_id=3,
            difficulty=Problem.Difficulty.MEDIUM,
            solved=False,
        )

        # 1. Output directory not configured
        with patch("userconfig.effective.output_dirs", return_value=[]):
            notes_metrics = get_notes_created_metrics()
            self.assertEqual(notes_metrics["notes_created"], 0)
            self.assertEqual(notes_metrics["total_solved"], 2)
            self.assertEqual(notes_metrics["coverage_pct"], 0.0)
            self.assertEqual(notes_metrics["pending_count"], 2)
            self.assertFalse(notes_metrics["output_dir_configured"])

        # 2. Output directory configured with temp directory
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_path = Path(tmp_dir)
            canonical = dir_key(out_path)

            with patch("userconfig.effective.output_dirs", return_value=[out_path]):
                # No files written yet
                notes_metrics = get_notes_created_metrics()
                self.assertEqual(notes_metrics["notes_created"], 0)
                self.assertEqual(notes_metrics["pending_count"], 2)

                # Write files on disk:
                # p1 has both problem note and solution note
                p1_prob_file = out_path / "Leetcode" / "problems" / "0001-two-sum.md"
                p1_sol_file = out_path / "Leetcode" / "solutions" / "0001-two-sum-solution.md"
                p1_prob_file.parent.mkdir(parents=True, exist_ok=True)
                p1_sol_file.parent.mkdir(parents=True, exist_ok=True)
                p1_prob_file.write_text("# Two Sum", encoding="utf-8")
                p1_sol_file.write_text("# Two Sum Solution", encoding="utf-8")

                now = timezone.now()
                NoteRecord.objects.create(
                    problem=p1,
                    kind=NoteRecord.Kind.PROBLEM,
                    output_dir=canonical,
                    relative_path="Leetcode/problems/0001-two-sum.md",
                    content_hash=content_hash("# Two Sum"),
                    written_at=now,
                )
                NoteRecord.objects.create(
                    problem=p1,
                    kind=NoteRecord.Kind.SOLUTION,
                    output_dir=canonical,
                    relative_path="Leetcode/solutions/0001-two-sum-solution.md",
                    content_hash=content_hash("# Two Sum Solution"),
                    written_at=now,
                )

                # p2 (solved) has solution note only
                p2_sol_file = out_path / "Leetcode" / "solutions" / "0002-add-two-numbers-solution.md"
                p2_sol_file.write_text("# Add Two Numbers Solution", encoding="utf-8")
                NoteRecord.objects.create(
                    problem=p2,
                    kind=NoteRecord.Kind.SOLUTION,
                    output_dir=canonical,
                    relative_path="Leetcode/solutions/0002-add-two-numbers-solution.md",
                    content_hash=content_hash("# Add Two Numbers Solution"),
                    written_at=now,
                )

                # p3 (unsolved) has problem note only
                p3_prob_file = out_path / "Leetcode" / "problems" / "0003-longest-substring.md"
                p3_prob_file.write_text("# Longest Substring", encoding="utf-8")
                NoteRecord.objects.create(
                    problem=p3,
                    kind=NoteRecord.Kind.PROBLEM,
                    output_dir=canonical,
                    relative_path="Leetcode/problems/0003-longest-substring.md",
                    content_hash=content_hash("# Longest Substring"),
                    written_at=now,
                )

                # p1 has 2 notes but counts +1. p2 is solved and counts +1. p3 is unsolved and does not count toward solved notes.
                notes_metrics = get_notes_created_metrics()
                self.assertEqual(notes_metrics["notes_created"], 2)
                self.assertEqual(notes_metrics["total_solved"], 2)
                self.assertEqual(notes_metrics["coverage_pct"], 100.0)
                self.assertEqual(notes_metrics["pending_count"], 0)
                self.assertTrue(notes_metrics["output_dir_configured"])

                # 3. Simulate user deleting p2's note file on disk -> classified as MISSING
                p2_sol_file.unlink()
                notes_metrics = get_notes_created_metrics()
                self.assertEqual(notes_metrics["notes_created"], 1)
                self.assertEqual(notes_metrics["pending_count"], 1)
                self.assertEqual(notes_metrics["coverage_pct"], 50.0)

            # 4. User changes output directory to a different folder
            with tempfile.TemporaryDirectory() as other_dir:
                with patch("userconfig.effective.output_dirs", return_value=[Path(other_dir)]):
                    notes_metrics = get_notes_created_metrics()
                    self.assertEqual(notes_metrics["notes_created"], 0)
                    self.assertEqual(notes_metrics["pending_count"], 2)
                    self.assertEqual(notes_metrics["coverage_pct"], 0.0)

        # 5. AI Prefills metrics test
        AIResponse.objects.create(
            problem=p1,
            source=AIResponse.Source.PROVIDER,
            provider_name="claude",
            model_name="claude-3-5-sonnet",
            prompt_version="v1",
            input_hash="hash1",
            raw_text="solution 1",
            status=AIResponse.Status.VALID,
            is_active=True,
        )
        AIResponse.objects.create(
            problem=p1,
            source=AIResponse.Source.PROVIDER,
            provider_name="agy",
            model_name="gemini-2.5-flash",
            prompt_version="v1",
            input_hash="hash2",
            raw_text="solution 2 candidate",
            status=AIResponse.Status.VALID,
            is_active=False,
        )
        AIResponse.objects.create(
            problem=p2,
            source=AIResponse.Source.MANUAL,
            provider_name="manual",
            prompt_version="v1",
            input_hash="hash3",
            raw_text="manual solution",
            status=AIResponse.Status.VALID,
            is_active=True,
        )

        prefills_metrics = get_ai_prefills_metrics()
        self.assertEqual(prefills_metrics["total"], 3)
        self.assertEqual(prefills_metrics["active"], 2)
        self.assertEqual(prefills_metrics["problems_covered"], 2)

        # 6. Aggregated dashboard metrics
        dashboard_metrics = get_dashboard_metrics()
        self.assertIn("notes", dashboard_metrics)
        self.assertIn("prefills", dashboard_metrics)
        self.assertEqual(dashboard_metrics["prefills"]["total"], 3)

    @patch("ui.views.effective.leetcode_session", return_value="fake_session")
    @patch("ui.views.effective.leetcode_csrf_token", return_value="fake_csrf")
    @patch("ui.views.get_client")
    @patch("ui.views.sync_leetcode_overview")
    def test_sync_leetcode_data_view_error(self, mock_sync, mock_client, mock_csrf, mock_session):
        from leetcode.exceptions import LeetCodeError
        mock_sync.side_effect = LeetCodeError("Network timeout connecting to LeetCode")
        url = reverse("ui:sync_leetcode_data")
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "ui/partials/sync_leetcode_button.html")
        self.assertContains(response, "Sync failed: Network timeout connecting to LeetCode")
        self.assertContains(response, 'id="toast-container"')

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
        cache.clear()
        self.client = Client()

        def cleanup_test_snapshots():
            from userconfig.paths import db_snapshots_dir
            for path in db_snapshots_dir().glob("*test_snap*.tar.gz"):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass

        self.addCleanup(cleanup_test_snapshots)

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
            real_name="Test User",
            display_name="Test User (test_user)",
            is_premium=False,
            message="Connected as Test User (test_user).",
        )
        url = reverse("ui:test_connection")
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(
            response.content,
            {
                "ok": True,
                "state": "connected",
                "message": "Connected as Test User (test_user).",
                "username": "test_user",
                "real_name": "Test User",
                "display_name": "Test User (test_user)",
                "is_premium": False,
                "cached": False,
            },
        )

    @patch("ui.helpers.settings.check_connection")
    def test_connection_debounce_caching(self, mock_conn):
        """Verify repeated calls within cooldown return cached result without hitting LeetCode."""
        mock_conn.return_value = mock.MagicMock(
            ok=True,
            state=mock.MagicMock(value="connected"),
            username="test_user",
            real_name="Test User",
            display_name="Test User (test_user)",
            is_premium=False,
            message="Connected as Test User (test_user).",
        )
        url = reverse("ui:test_connection")

        # First call: hits LeetCode, cached=False
        res1 = self.client.get(url)
        self.assertEqual(res1.status_code, 200)
        self.assertFalse(res1.json()["cached"])
        self.assertEqual(mock_conn.call_count, 1)

        # Second call immediately after: debounced from cache, cached=True
        res2 = self.client.get(url)
        self.assertEqual(res2.status_code, 200)
        self.assertTrue(res2.json()["cached"])
        self.assertEqual(mock_conn.call_count, 1)  # Still 1! Not hit again!

        # Third call with force=true: bypasses cache, cached=False
        res3 = self.client.get(url + "?force=true")
        self.assertEqual(res3.status_code, 200)
        self.assertFalse(res3.json()["cached"])
        self.assertEqual(mock_conn.call_count, 2)  # Hit again!

    @patch("ui.helpers.settings.check_connection")
    def test_connection_failure_never_cached(self, mock_conn):
        """Verify that connection failures/expired sessions are never cached."""
        mock_conn.return_value = mock.MagicMock(
            ok=False,
            state=mock.MagicMock(value="signed_out"),
            username="",
            real_name="",
            display_name="",
            is_premium=False,
            message="Cookies have expired.",
        )
        url = reverse("ui:test_connection")

        # First failure check: live call
        res1 = self.client.get(url)
        self.assertEqual(res1.status_code, 200)
        self.assertFalse(res1.json()["cached"])
        self.assertEqual(mock_conn.call_count, 1)

        # Second failure check immediately after: not cached, calls live again!
        res2 = self.client.get(url)
        self.assertEqual(res2.status_code, 200)
        self.assertFalse(res2.json()["cached"])
        self.assertEqual(mock_conn.call_count, 2)

    @patch("ui.helpers.settings.check_connection")
    def test_dashboard_connection_endpoint_success(self, mock_conn):
        """Verify the dashboard connection returns the partial with display name and no continuous poll."""
        mock_conn.return_value = mock.MagicMock(
            ok=True,
            state=mock.MagicMock(value="connected"),
            username="ajeet",
            real_name="Ajeet Patel",
            display_name="Ajeet Patel (ajeet)",
            is_premium=True,
            message="Connected as Ajeet Patel (ajeet).",
        )
        url = reverse("ui:test_connection") + "?source=dashboard"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "ui/partials/dashboard_connection_status.html")
        content = response.content.decode("utf-8")
        self.assertIn('id="dashboard-leetcode-status"', content)
        self.assertNotIn('hx-trigger="every 5s"', content)
        self.assertNotIn('hx-trigger="load"', content)
        self.assertIn("Connected as Ajeet Patel (ajeet)", content)
        self.assertIn("Premium", content)

    @patch("ui.helpers.settings.check_connection")
    def test_dashboard_connection_endpoint_failure(self, mock_conn):
        """Verify the dashboard connection displays error status when connection fails."""
        mock_conn.return_value = mock.MagicMock(
            ok=False,
            state=mock.MagicMock(value="signed_out"),
            username="",
            real_name="",
            display_name="",
            is_premium=False,
            message="Cookies have expired.",
        )
        url = reverse("ui:test_connection") + "?source=dashboard"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "ui/partials/dashboard_connection_status.html")
        content = response.content.decode("utf-8")
        self.assertIn('id="dashboard-leetcode-status"', content)
        self.assertNotIn('hx-trigger="every 5s"', content)
        self.assertNotIn('hx-trigger="load"', content)
        self.assertIn("Cookies have expired.", content)
        self.assertIn("Disconnected", content)

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
        self.assertIn("db_counts", ctx)
        self.assertIn("problems", ctx["db_counts"])
        self.assertIn("submissions", ctx["db_counts"])
        self.assertIn("ai_responses", ctx["db_counts"])
        self.assertIn("note_records", ctx["db_counts"])

    def test_export_snapshot_to_folder(self):
        # Fetch URL for the export snapshot endpoint
        url = reverse("ui:export_snapshot")
        response = self.client.post(
            url,
            {
                "destination": "folder",
                "filename": "custom_test_snap.tar.gz",
                "include_submissions": "on",
                "include_ai_notes": "on",
            },
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("Snapshot saved to db_snapshots/custom_test_snap.tar.gz", content)
        self.assertIn('id="snapshots-list-container"', content)
        self.assertIn('id="snapshot-dropdown-container"', content)
        self.assertIn('data-persistent="true"', content)

    def test_export_snapshot_download_file(self):
        # Fetch URL for the export snapshot endpoint
        url = reverse("ui:export_snapshot")
        response = self.client.post(
            url,
            {
                "destination": "download",
                "filename": "download_test_snap.tar.gz",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/gzip")
        self.assertIn("download_test_snap.tar.gz", response["Content-Disposition"])

    def test_export_complete_database_option(self):
        # Fetch URL for the export snapshot endpoint
        url = reverse("ui:export_snapshot")
        response = self.client.post(
            url,
            {
                "destination": "folder",
                "filename": "complete_test_snap.tar.gz",
                "export_all": "on",
            },
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("Snapshot saved to db_snapshots/complete_test_snap.tar.gz", content)
        self.assertIn('data-persistent="true"', content)

    def test_refresh_snapshots_endpoint(self):
        # Fetch URL for refreshing snapshots list
        url = reverse("ui:refresh_snapshots")
        response = self.client.get(url, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("Refreshed:", content)
        self.assertIn('id="snapshot-dropdown-container"', content)
        self.assertIn('id="snapshots-list-container"', content)
        self.assertIn('data-persistent="true"', content)

    def test_download_database_file_endpoint(self):
        # Fetch URL for downloading raw SQLite database file
        url = reverse("ui:download_database_file")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/x-sqlite3")

    def test_import_snapshot_endpoint(self):
        # First export a snapshot to use as input
        # Fetch URL for export snapshot
        export_url = reverse("ui:export_snapshot")
        self.client.post(
            export_url,
            {
                "destination": "folder",
                "filename": "import_test_snap.tar.gz",
            },
            HTTP_HX_REQUEST="true",
        )

        # Now import it via the import endpoint
        # Fetch URL for import snapshot
        import_url = reverse("ui:import_snapshot")
        response = self.client.post(
            import_url,
            {
                "source": "snapshot_file",
                "snapshot_filename": "import_test_snap.tar.gz",
                "dry_run": "on",
            },
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("Dry Run Simulation", content)
        self.assertIn('data-persistent="true"', content)

    def test_reset_submissions_endpoint(self):
        # Fetch URL for reset submissions
        url = reverse("ui:reset_submissions")
        response = self.client.post(url, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("User submissions reset", content)
        self.assertIn('data-persistent="true"', content)

    def test_clear_notes_records_endpoint(self):
        # Fetch URL for clear note records
        url = reverse("ui:clear_notes_records")
        response = self.client.post(url, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertIn("note tracking records from the database", content)
        self.assertIn('data-persistent="true"', content)

    def test_reinit_database_endpoint(self):
        # Fetch URL for reinitializing database
        url = reverse("ui:reinit_database")
        with patch("ui.views.reinit_database", return_value={"success": True}):
            response = self.client.post(url, HTTP_HX_REQUEST="true")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["HX-Refresh"], "true")
