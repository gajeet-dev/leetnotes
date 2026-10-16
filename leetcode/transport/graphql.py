import structlog

from ..exceptions import (
    LeetCodeError,
    ResponseShapeError,
)
from .http import HttpClient

logger = structlog.get_logger(__name__)


class GraphQLClient:
    def __init__(self, http: HttpClient, path: str = "/graphql"):
        self._http = http
        self._path = path

    def execute(self, query, variables=None):
        body = self._http.post_json(
            self._path, {"query": query, "variables": variables or {}}
        )
        if errors := body.get("errors"):
            logger.warning("graphql_errors", errors=errors)
            raise LeetCodeError(f"GraphQL error: {errors[0].get('message')}")
        data = body.get("data")
        if data is None:
            raise ResponseShapeError("response had no 'data' field")
        return data
