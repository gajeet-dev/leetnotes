from unittest.mock import MagicMock

from django.test import SimpleTestCase

from leetcode.bootstrap import ClientSettings, build_client
from leetcode.exceptions import NotFoundError
from leetcode.resources.problems import ALL_PROBLEMS_PATH, ProblemsResource
from leetcode.transport.queries import QUESTION_DETAILS_QUERY
from leetcode.transport.session import (
    SessionConfig,
    create_adapter,
    create_public_session,
    create_session,
)


class ProblemsResourceTests(SimpleTestCase):
    def setUp(self):
        self.auth_gql = MagicMock()
        self.public_gql = MagicMock()
        self.http = MagicMock()
        self.resource = ProblemsResource(
            gql=self.auth_gql,
            http=self.http,
            public_gql=self.public_gql,
        )

    def test_get_problem_uses_public_gql_client(self):
        self.public_gql.execute.return_value = {
            "question": {
                "title": "Two Sum",
                "questionFrontendId": "1",
                "content": "<p>Two Sum Statement</p>",
            }
        }

        # Fetch problem details for two-sum using the resource
        result = self.resource.get("two-sum")

        self.assertEqual(result["title"], "Two Sum")
        self.public_gql.execute.assert_called_once_with(
            QUESTION_DETAILS_QUERY, {"titleSlug": "two-sum"}
        )
        self.auth_gql.execute.assert_not_called()

    def test_get_problem_raises_not_found_when_question_is_none(self):
        self.public_gql.execute.return_value = {"question": None}

        with self.assertRaises(NotFoundError):
            self.resource.get("non-existent-problem")

    def test_fallback_to_gql_when_public_gql_is_none(self):
        fallback_resource = ProblemsResource(
            gql=self.auth_gql,
            http=self.http,
            public_gql=None,
        )
        self.auth_gql.execute.return_value = {
            "question": {"title": "Fallback", "questionFrontendId": "2"}
        }

        result = fallback_resource.get("fallback-slug")

        self.assertEqual(result["title"], "Fallback")
        self.auth_gql.execute.assert_called_once_with(
            QUESTION_DETAILS_QUERY, {"titleSlug": "fallback-slug"}
        )

    def test_list_all_uses_authenticated_http_client(self):
        self.http.get_json.return_value = {
            "stat_status_pairs": [
                {"stat": {"question_id": 1}, "status": "ac"},
            ]
        }

        # Retrieve all problem pairs through the HTTP client
        pairs = self.resource.list_all()

        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["status"], "ac")
        self.http.get_json.assert_called_once_with(ALL_PROBLEMS_PATH)


class PublicSessionTests(SimpleTestCase):
    def test_create_public_session_has_no_cookies_or_csrf_token(self):
        config = SessionConfig(
            session_cookie="secret-session-value",
            csrf_token="secret-csrf-token",
            base_url="https://leetcode.com",
            requests_per_second=2.0,
        )
        adapter = create_adapter(config)
        public_session = create_public_session(config, adapter=adapter)

        # Confirm no sensitive cookies are in the public session's cookie jar
        self.assertNotIn("LEETCODE_SESSION", public_session.cookies)
        self.assertNotIn("csrftoken", public_session.cookies)

        # Confirm no X-CSRFToken header is present
        self.assertNotIn("X-CSRFToken", public_session.headers)

        # Confirm User-Agent and JSON headers are configured
        self.assertIn("User-Agent", public_session.headers)
        self.assertEqual(public_session.headers.get("Content-Type"), "application/json")

    def test_build_client_wires_public_and_auth_sessions(self):
        config = ClientSettings(
            session="user-session",
            csrf_token="user-csrf",
            base_url="https://leetcode.com",
            requests_per_second=1.0,
            requests_per_minute=30.0,
            username="testuser",
        )
        client = build_client(config)

        # Verify ProblemsResource public_gql uses a session with no cookies
        public_session = client._problems._public_gql._http._session
        self.assertNotIn("LEETCODE_SESSION", public_session.cookies)
        self.assertNotIn("csrftoken", public_session.cookies)
        self.assertNotIn("X-CSRFToken", public_session.headers)

        # Verify authenticated http uses a session with user cookies
        auth_session = client._problems._http._session
        self.assertEqual(
            auth_session.cookies.get("LEETCODE_SESSION", domain=".leetcode.com"),
            "user-session",
        )
        self.assertEqual(
            auth_session.cookies.get("csrftoken", domain=".leetcode.com"),
            "user-csrf",
        )
        self.assertEqual(auth_session.headers.get("X-CSRFToken"), "user-csrf")
