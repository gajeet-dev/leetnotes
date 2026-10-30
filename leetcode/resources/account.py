from typing import Any

from ..exceptions import ResponseShapeError
from ..transport.graphql import GraphQLClient
from ..transport.queries import USER_STATUS_QUERY


class AccountResource:
    def __init__(self, gql: GraphQLClient):
        self._gql = gql

    def status(self) -> dict[str, Any]:
        """The raw `userStatus` of whoever the cookies belong to."""
        data = self._gql.execute(USER_STATUS_QUERY)
        status = data.get("userStatus")
        if status is None:
            raise ResponseShapeError("response had no 'userStatus'")
        return status
