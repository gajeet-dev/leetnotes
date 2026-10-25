from django.test import override_settings

from leetcode.bootstrap import get_client, reset_client
from leetcode.exceptions import NotConfigured
from userconfig import update_config, update_credentials
from userconfig.tests.helpers import DataDirTestCase

NO_ENV_VALUES = {
    "LEETCODE_SESSION": "",
    "LEETCODE_CSRF_TOKEN": "",
    "LEETCODE_USERNAME": "",
}


@override_settings(**NO_ENV_VALUES)
class GetClientTests(DataDirTestCase):
    def setUp(self):
        super().setUp()
        reset_client()
        self.addCleanup(reset_client)

    def save_cookies(self):
        update_credentials(session="session-value", csrf_token="csrf-value")

    def test_no_cookies_is_a_clear_error(self):
        with self.assertRaisesMessage(NotConfigured, "Settings"):
            get_client()

    def test_one_cookie_is_not_enough(self):
        update_credentials(session="only-a-session")
        with self.assertRaises(NotConfigured):
            get_client()

    def test_the_same_settings_reuse_one_client(self):
        self.save_cookies()
        self.assertIs(get_client(), get_client())

    def test_a_new_cookie_builds_a_new_client(self):
        self.save_cookies()
        first = get_client()
        update_credentials(session="another-session")
        self.assertIsNot(get_client(), first)

    def test_changing_the_pacing_builds_a_new_client(self):
        self.save_cookies()
        first = get_client()
        update_config({"requests_per_minute": 5})
        self.assertIsNot(get_client(), first)

    def test_cookies_from_env_still_work(self):
        with override_settings(
            LEETCODE_SESSION="env-session", LEETCODE_CSRF_TOKEN="env-csrf"
        ):
            self.assertIsNotNone(get_client())
