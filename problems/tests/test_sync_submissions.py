from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from django.test import TestCase

from leetcode.dto import RecentAcDTO, SubmissionDetailDTO, SubmissionSummaryDTO
from problems.models import Problem, Submission, SyncTask
from problems.services.sync import (
    Deps,
    DiscoveryResult,
    RecentCheckResult,
    _sync_submissions,
    refresh_recent_submissions,
    sync_leetcode_overview,
)


class SyncSubmissionsTests(TestCase):
    def setUp(self):
        self.problem = Problem.objects.create(slug="two-sum", leetcode_id=1, title="Two Sum")
        self.task = SyncTask.objects.create(
            problem=self.problem,
            part=SyncTask.Part.SUBMISSIONS,
            status=SyncTask.Status.PENDING,
        )
        self.client = MagicMock()
        self.downloader = MagicMock()
        self.deps = Deps(client=self.client, downloader=self.downloader)

    def test_pinned_solution_skips_network_and_completes(self):
        now = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)
        Submission.objects.create(
            problem=self.problem,
            lang="python3",
            code="pinned code",
            submission_date=now,
            pinned=True,
        )
        _sync_submissions(self.task, self.deps)
        self.client.iter_submissions.assert_not_called()
        self.client.get_submission_details.assert_not_called()
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, SyncTask.Status.DONE)

    def test_no_accepted_submissions_found_completes_task(self):
        self.client.iter_submissions.return_value = [
            SubmissionSummaryDTO(
                id=1,
                status="Wrong Answer",
                lang="python3",
                timestamp=datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC),
            )
        ]
        _sync_submissions(self.task, self.deps)
        self.client.get_submission_details.assert_not_called()
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, SyncTask.Status.DONE)

    def test_already_stored_within_timestamp_window_skips_detail_fetch(self):
        stored_time = datetime(2026, 10, 28, 12, 0, 1, tzinfo=UTC)
        Submission.objects.create(
            problem=self.problem,
            lang="python3",
            code="stored code",
            submission_date=stored_time,
        )
        list_time = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)
        self.client.iter_submissions.return_value = [
            SubmissionSummaryDTO(
                id=123,
                status="Accepted",
                lang="python3",
                timestamp=list_time,
            )
        ]
        _sync_submissions(self.task, self.deps)
        self.client.get_submission_details.assert_not_called()
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, SyncTask.Status.DONE)
        self.assertEqual(Submission.objects.filter(problem=self.problem).count(), 1)
        sub = self.problem.submission
        self.assertEqual(sub.code, "stored code")

    def test_detail_fetched_when_new_submission(self):
        now = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)
        self.client.iter_submissions.return_value = [
            SubmissionSummaryDTO(
                id=123,
                status="Accepted",
                lang="python3",
                timestamp=now,
            )
        ]
        self.client.get_submission_details.return_value = SubmissionDetailDTO(
            lang="python3",
            code="print('hello')",
            submission_date=now + timedelta(seconds=1),
        )
        _sync_submissions(self.task, self.deps)
        self.client.get_submission_details.assert_called_once_with(123)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, SyncTask.Status.DONE)
        self.problem.refresh_from_db()
        sub = self.problem.submission
        self.assertIsNotNone(sub)
        self.assertEqual(sub.code, "print('hello')")
        self.assertEqual(sub.submission_date, now + timedelta(seconds=1))

    def test_detail_fetched_replaces_older_unpinned_submissions(self):
        old_time = datetime(2026, 10, 20, 12, 0, 0, tzinfo=UTC)
        Submission.objects.create(
            problem=self.problem,
            lang="python3",
            code="old code",
            submission_date=old_time,
        )
        new_time = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)
        self.client.iter_submissions.return_value = [
            SubmissionSummaryDTO(
                id=456,
                status="Accepted",
                lang="java",
                timestamp=new_time,
            )
        ]
        self.client.get_submission_details.return_value = SubmissionDetailDTO(
            lang="java",
            code="class Solution {}",
            submission_date=new_time,
        )
        _sync_submissions(self.task, self.deps)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, SyncTask.Status.DONE)
        self.assertEqual(Submission.objects.filter(problem=self.problem).count(), 1)
        self.problem.refresh_from_db()
        sub = self.problem.submission
        self.assertEqual(sub.lang, "java")
        self.assertEqual(sub.code, "class Solution {}")

    def test_idempotent_when_submission_with_exact_date_already_exists(self):
        date = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)
        Submission.objects.create(
            problem=self.problem,
            lang="python3",
            code="previous code",
            submission_date=date,
        )
        self.client.iter_submissions.return_value = [
            SubmissionSummaryDTO(
                id=789,
                status="Accepted",
                lang="python3",
                timestamp=date + timedelta(seconds=30),
            )
        ]
        self.client.get_submission_details.return_value = SubmissionDetailDTO(
            lang="python3",
            code="updated code",
            submission_date=date,
        )
        _sync_submissions(self.task, self.deps)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, SyncTask.Status.DONE)
        self.assertEqual(Submission.objects.filter(problem=self.problem).count(), 1)
        self.problem.refresh_from_db()
        sub = self.problem.submission
        self.assertEqual(sub.code, "updated code")

    def test_sync_submissions_persists_submission_id_and_resets_stale(self):
        date = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)
        self.client.iter_submissions.return_value = [
            SubmissionSummaryDTO(
                id=12345,
                status="Accepted",
                lang="python3",
                timestamp=date,
            )
        ]
        self.client.get_submission_details.return_value = SubmissionDetailDTO(
            lang="python3",
            code="solution code",
            submission_date=date,
        )
        _sync_submissions(self.task, self.deps)
        self.problem.refresh_from_db()
        sub = self.problem.submission
        self.assertEqual(sub.submission_id, 12345)
        self.assertFalse(sub.is_stale)
        self.assertIsNone(sub.newer_submission_id)
        self.assertIsNone(sub.newer_submission_date)

    def test_refresh_recent_submissions_marks_stale_and_records_newer_id(self):
        old_time = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)
        new_time = datetime(2026, 11, 1, 14, 0, 0, tzinfo=UTC)
        Submission.objects.create(
            problem=self.problem,
            submission_id=100,
            lang="python3",
            code="old code",
            submission_date=old_time,
        )
        self.problem.solved = True
        self.problem.save()

        self.client.get_recent_accepted.return_value = [
            RecentAcDTO(
                id=200,
                slug=self.problem.slug,
                title=self.problem.title,
                timestamp=new_time,
            )
        ]

        result = refresh_recent_submissions(self.client)
        self.assertEqual(result.stale_detected, 1)
        self.assertEqual(result.up_to_date, 0)
        self.assertEqual(result.ignored_same_newer, 0)

        sub = Submission.objects.get(problem=self.problem)
        self.assertTrue(sub.is_stale)
        self.assertEqual(sub.newer_submission_id, 200)
        self.assertEqual(sub.newer_submission_date, new_time)

    def test_refresh_recent_submissions_deduplicates_same_newer_id(self):
        old_time = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)
        new_time = datetime(2026, 11, 1, 14, 0, 0, tzinfo=UTC)
        Submission.objects.create(
            problem=self.problem,
            submission_id=100,
            lang="python3",
            code="old code",
            submission_date=old_time,
            is_stale=False,  # e.g. user dismissed / decided to keep old solution
            newer_submission_id=200,
            newer_submission_date=new_time,
        )
        self.problem.solved = True
        self.problem.save()

        self.client.get_recent_accepted.return_value = [
            RecentAcDTO(
                id=200,
                slug=self.problem.slug,
                title=self.problem.title,
                timestamp=new_time,
            )
        ]

        result = refresh_recent_submissions(self.client)
        self.assertEqual(result.stale_detected, 0)
        self.assertEqual(result.ignored_same_newer, 1)

        sub = Submission.objects.get(problem=self.problem)
        self.assertFalse(sub.is_stale)
        self.assertEqual(sub.newer_submission_id, 200)

    def test_refresh_recent_submissions_pinned_with_updates(self):
        old_time = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)
        new_time = datetime(2026, 11, 1, 14, 0, 0, tzinfo=UTC)
        Submission.objects.create(
            problem=self.problem,
            submission_id=100,
            lang="python3",
            code="pinned code",
            submission_date=old_time,
            pinned=True,
        )
        self.problem.solved = True
        self.problem.save()

        self.client.get_recent_accepted.return_value = [
            RecentAcDTO(
                id=300,
                slug=self.problem.slug,
                title=self.problem.title,
                timestamp=new_time,
            )
        ]

        result = refresh_recent_submissions(self.client)
        self.assertEqual(result.stale_detected, 0)
        self.assertEqual(result.pinned_with_updates, 1)
        self.assertEqual(result.pinned_skipped, 1)

        sub = Submission.objects.get(problem=self.problem)
        self.assertTrue(sub.is_stale)
        self.assertEqual(sub.newer_submission_id, 300)

    def test_refresh_recent_submissions_already_up_to_date(self):
        date = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)
        Submission.objects.create(
            problem=self.problem,
            submission_id=100,
            lang="python3",
            code="code",
            submission_date=date,
        )
        self.problem.solved = True
        self.problem.save()

        self.client.get_recent_accepted.return_value = [
            RecentAcDTO(
                id=100,
                slug=self.problem.slug,
                title=self.problem.title,
                timestamp=date,
            )
        ]

        result = refresh_recent_submissions(self.client)
        self.assertEqual(result.up_to_date, 1)
        self.assertEqual(result.stale_detected, 0)

    @patch("problems.services.sync.discover")
    @patch("problems.services.sync.refresh_recent_submissions")
    def test_sync_leetcode_overview_orchestrates(self, mock_recent, mock_disc):
        mock_disc.return_value = DiscoveryResult(
            total=100,
            new_problems=5,
            newly_solved=2,
            new_tasks=10,
        )
        mock_recent.return_value = RecentCheckResult(
            checked=20,
            stale_detected=3,
            ignored_same_newer=1,
            pinned_with_updates=1,
            up_to_date=15,
        )

        res = sync_leetcode_overview(self.client, recent_limit=20)
        self.assertEqual(res.total_catalog, 100)
        self.assertEqual(res.new_problems, 5)
        self.assertEqual(res.newly_solved, 2)
        self.assertEqual(res.new_tasks, 10)
        self.assertEqual(res.recent_checked, 20)
        self.assertEqual(res.stale_detected, 3)
        self.assertEqual(res.ignored_same_newer, 1)
        self.assertEqual(res.pinned_with_updates, 1)
        self.assertEqual(res.up_to_date, 15)
