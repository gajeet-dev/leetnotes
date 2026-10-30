from django.test import SimpleTestCase

from leetcode.exceptions import ResponseShapeError
from leetcode.mappers.account import map_account_status
from leetcode.resources.account import AccountResource


class MapAccountStatusTests(SimpleTestCase):
    def test_signed_in(self):
        account = map_account_status(
            {"isSignedIn": True, "username": "ajeet", "isPremium": False}
        )
        self.assertEqual(
            (account.signed_in, account.username, account.is_premium),
            (True, "ajeet", False),
        )

    def test_signed_out_with_a_null_username(self):
        account = map_account_status(
            {"isSignedIn": False, "username": None, "isPremium": None}
        )
        self.assertEqual(
            (account.signed_in, account.username, account.is_premium),
            (False, "", False),
        )

    def test_missing_fields_mean_signed_out(self):
        self.assertFalse(map_account_status({}).signed_in)


class FakeGraphQL:
    def __init__(self, data):
        self.data, self.queries = data, []

    def execute(self, query, variables=None):
        self.queries.append(query)
        return self.data


class AccountResourceTests(SimpleTestCase):
    def test_returns_the_user_status(self):
        fake = FakeGraphQL({"userStatus": {"isSignedIn": True}})
        self.assertEqual(AccountResource(fake).status(), {"isSignedIn": True})
        self.assertIn("userStatus", fake.queries[0])

    def test_a_response_without_user_status_is_a_shape_error(self):
        with self.assertRaises(ResponseShapeError):
            AccountResource(FakeGraphQL({})).status()
