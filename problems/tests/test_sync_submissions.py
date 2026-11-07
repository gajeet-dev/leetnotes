from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from django.test import TestCase

from leetcode.dto import SubmissionDetailDTO, SubmissionSummaryDTO
from problems.models import Problem, Submission, SyncTask
from problems.services.sync import Deps, _sync_submissions


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
        self.assertEqual(self.problem.submissions.count(), 1)

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
        sub = self.problem.submissions.first()
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
        self.assertEqual(self.problem.submissions.count(), 1)
        sub = self.problem.submissions.first()
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
        self.assertEqual(self.problem.submissions.count(), 1)
        sub = self.problem.submissions.first()
        self.assertEqual(sub.code, "updated code")
