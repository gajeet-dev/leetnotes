from django.db import transaction

from leetcode import get_client
from leetcode.dto import RecentAcDTO
from problems.models import Problem, Submission


def fetch_recent_submissions_with_db_updates() -> list[RecentAcDTO]:
    """Fetches the live recent submission list from leetcode API
    and updates the db with problem as solved and stale for new submissions"""

    recent_ac_list: list[RecentAcDTO] = get_client().get_recent_accepted()

    slugs = {item.slug for item in recent_ac_list}

    Problem.objects.filter(
        slug__in=slugs,
    ).mark_as_solved()

    update_stale_submissions(recent_ac_list)

    return recent_ac_list


def update_stale_submissions(recent_ac_list: list[RecentAcDTO]) -> int:
    """
    Compare recent LeetCode submissions with existing database records.

    Mark submissions as stale when a newer accepted submission exists
    and the existing submission is not pinned.

    Returns the number of updated records.
    """

    slugs = {item.slug for item in recent_ac_list}

    submissions_by_slug = {
        submission.problem.slug: submission
        for submission in (
            Submission.objects.filter(
                problem__slug__in=slugs,
            ).select_related("problem")
        )
    }
    submissions_to_update = []

    for item in recent_ac_list:
        submission = submissions_by_slug.get(item.slug)

        if submission is None:
            continue

        if submission.pinned:
            continue

        if submission.newer_submission_id == item.id:
            continue

        if (
            submission.newer_submission_date is not None
            and submission.newer_submission_date >= item.timestamp
        ):
            continue

        submission.is_stale = True
        submission.newer_submission_id = item.id
        submission.newer_submission_date = item.timestamp

        submissions_to_update.append(submission)

    if submissions_to_update:
        with transaction.atomic():
            Submission.objects.bulk_update(
                submissions_to_update,
                fields=[
                    "is_stale",
                    "newer_submission_id",
                    "newer_submission_date",
                ],
            )

    return len(submissions_to_update)
