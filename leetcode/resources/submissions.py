from collections.abc import Iterator
from typing import Any

import structlog

from ..exceptions import NotFoundError
from ..graphql import GraphQLClient
from ..queries import (
    RECENT_AC_SUBMISSIONS_QUERY,
    SUBMISSION_DETAILS_QUERY,
    SUBMISSION_LIST_QUERY,
)

logger = structlog.get_logger(__name__)


class SubmissionsResource:
    def __init__(self, gql: GraphQLClient, default_username: str | None = None):
        self._gql = gql
        self._default_username = default_username

    def list_page(
        self, slug: str, *, limit: int = 20, offset: int = 0
    ) -> list[dict[str, Any]]:
        """Return one page of submission history for a problem."""

        data = self._gql.execute(
            SUBMISSION_LIST_QUERY,
            {"questionSlug": slug, "limit": limit, "offset": offset},
        )
        submissions = (data.get("questionSubmissionList") or {}).get(
            "submissions"
        ) or []

        logger.info(
            "submission_list_request_succeeded",
            slug=slug,
            submission_count=len(submissions),
        )
        return submissions

    def iter_all(self, slug: str, *, page_size: int = 20) -> Iterator[dict[str, Any]]:
        offset = 0
        while True:
            page = self.list_page(slug, limit=page_size, offset=offset)
            yield from page
            if len(page) < page_size:
                return
            offset += page_size

    def recent_accepted(
        self, username: str | None = None, *, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Return a user's most recent accepted submissions."""

        target = username or self._default_username
        if not target:
            raise ValueError(
                "No LeetCode username available — "
                "pass one explicitly or set LEETCODE_USERNAME."
            )

        data = self._gql.execute(
            RECENT_AC_SUBMISSIONS_QUERY, {"username": target, "limit": limit}
        )
        submissions = data.get("recentAcSubmissionList") or []

        if not submissions:
            logger.warning(
                "recent_ac_submissions_empty_result",
                action="forcing_auth_recheck",
            )

        logger.info(
            "recent_ac_submissions_request_succeeded",
            username=target,
            submission_count=len(submissions),
        )
        return submissions

    def get_details(self, submission_id: int) -> dict[str, Any]:
        """Return source code and metadata for a submission."""

        data = self._gql.execute(
            SUBMISSION_DETAILS_QUERY, {"submissionId": int(submission_id)}
        )
        details = data.get("submissionDetails")

        if not details:
            raise NotFoundError(f"submission not found: {submission_id}")

        logger.info("submission_details_request_succeeded", submission_id=submission_id)
        return details
