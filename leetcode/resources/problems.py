from typing import Any

import structlog

from ..exceptions import NotFoundError
from ..transport.graphql import GraphQLClient
from ..transport.http import HttpClient
from ..transport.queries import QUESTION_DETAILS_QUERY

logger = structlog.get_logger(__name__)

ALL_PROBLEMS_PATH = "/api/problems/all/"


class ProblemsResource:
    def __init__(self, gql: GraphQLClient, http: HttpClient):
        self._gql = gql
        self._http = http

    def get(self, slug: str) -> dict[str, Any]:
        """Return complete metadata for a problem."""
        data = self._gql.execute(QUESTION_DETAILS_QUERY, {"titleSlug": slug})
        question = data.get("question")

        if not question:
            logger.warning("question_details_not_found", slug=slug)
            raise NotFoundError(f"problem not found: {slug}")

        logger.info(
            "question_details_request_succeeded",
            slug=slug,
            title=question.get("title"),
        )
        return question

    def list_all(self) -> list[dict[str, Any]]:
        """Return the raw stat/status pairs for all problems."""
        logger.info("all_questions_request_started")
        data = self._http.get_json(ALL_PROBLEMS_PATH)

        solved = [
            pair
            for pair in data.get("stat_status_pairs") or []
        ]

        if not solved:
            # TODO(auth): an empty user-scoped result can mean an expired
            # session; re-add the forced auth recheck when auth is rebuilt.
            logger.warning("all_questions_empty_result")

        logger.info("all_questions_request_succeeded", solved_count=len(solved))
        return solved
