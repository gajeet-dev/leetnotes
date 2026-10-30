from django.test import override_settings

from leetcode.connection import ConnectionState, check_connection
from leetcode.dto import AccountStatusDTO
from leetcode.exceptions import (
    AuthError,
    NotConfigured,
    RateLimitError,
    ResponseShapeError,
    TransportError,
)
from userconfig import update_config
from userconfig.tests.helpers import DataDirTestCase

AJEET = AccountStatusDTO(signed_in=True, username="ajeet", is_premium=True)


class FakeClient:
    def __init__(self, status=None, error=None):
        self.status, self.error = status, error

    def get_account_status(self):
        if self.error:
            raise self.error
        return self.status


@override_settings(LEETCODE_USERNAME="")
class CheckConnectionTests(DataDirTestCase):
    def check(self, status=None, error=None):
        return check_connection(client_factory=lambda: FakeClient(status, error))

    def test_connected(self):
        result = self.check(AJEET)
        self.assertTrue(result.ok)
        self.assertEqual((result.username, result.is_premium), ("ajeet", True))
        self.assertIn("Connected as ajeet", result.message)

    def test_signed_out_when_leetcode_says_so(self):
        result = self.check(AccountStatusDTO(signed_in=False))
        self.assertEqual(result.state, ConnectionState.SIGNED_OUT)
        self.assertIn("expired", result.message)

    def test_an_auth_error_means_signed_out(self):
        self.assertEqual(
            self.check(error=AuthError("HTTP 403")).state, ConnectionState.SIGNED_OUT
        )

    def test_missing_cookies(self):
        def no_cookies():
            raise NotConfigured("cookies are not set")

        result = check_connection(client_factory=no_cookies)
        self.assertEqual(result.state, ConnectionState.NOT_CONFIGURED)
        self.assertEqual(result.message, "cookies are not set")

    def test_rate_limited(self):
        self.assertEqual(
            self.check(error=RateLimitError("429")).state, ConnectionState.RATE_LIMITED
        )

    def test_unreachable_includes_the_reason(self):
        result = self.check(error=TransportError("timed out"))
        self.assertEqual(result.state, ConnectionState.UNREACHABLE)
        self.assertIn("timed out", result.message)

    def test_an_unrecognised_answer(self):
        result = self.check(error=ResponseShapeError("no userStatus"))
        self.assertEqual(result.state, ConnectionState.UNEXPECTED)

    def test_a_different_saved_username_is_flagged(self):
        update_config({"leetcode_username": "someone-else"})
        result = self.check(AJEET)
        self.assertTrue(result.ok)
        self.assertTrue(result.username_differs)
        self.assertIn("someone-else", result.message)

    def test_the_username_comparison_ignores_case(self):
        update_config({"leetcode_username": " AJEET "})
        self.assertFalse(self.check(AJEET).username_differs)

    def test_no_saved_username_is_not_a_difference(self):
        self.assertFalse(self.check(AJEET).username_differs)

    def test_the_result_is_timestamped(self):
        self.assertIsNotNone(self.check(AJEET).checked_at.tzinfo)
