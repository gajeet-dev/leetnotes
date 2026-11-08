from pathlib import Path

from userconfig import ConfigError, effective, update_config, update_credentials
from userconfig.paths import PROJECT_ROOT
from userconfig.tests.helpers import DataDirTestCase


class EffectiveSettingsTests(DataDirTestCase):
    def test_default_settings_when_nothing_is_saved(self):
        self.assertEqual(effective.output_dirs(), [PROJECT_ROOT / "notes_output"])
        self.assertEqual(effective.leetcode_username(), "")
        self.assertEqual(effective.requests_per_second(), 1.0)
        self.assertEqual(effective.requests_per_minute(), 20.0)
        self.assertEqual(effective.problems_per_minute(), 5.0)
        self.assertEqual(effective.leetcode_session(), "")
        self.assertEqual(effective.leetcode_csrf_token(), "")

    def test_saved_values_win(self):
        update_config(
            {
                "output_dirs": ["/saved/vault"],
                "leetcode_username": "saved-user",
                "requests_per_second": 0.5,
                "requests_per_minute": 10,
                "problems_per_minute": 3,
            }
        )
        update_credentials(session="saved-session", csrf_token="saved-csrf")
        self.assertEqual(effective.output_dirs(), [Path("/saved/vault")])
        self.assertEqual(effective.leetcode_username(), "saved-user")
        self.assertEqual(effective.requests_per_second(), 0.5)
        self.assertEqual(effective.requests_per_minute(), 10)
        self.assertEqual(effective.problems_per_minute(), 3)
        self.assertEqual(effective.leetcode_session(), "saved-session")
        self.assertEqual(effective.leetcode_csrf_token(), "saved-csrf")

    def test_each_setting_falls_back_to_defaults_on_its_own(self):
        update_config({"leetcode_username": "saved-user"})
        self.assertEqual(effective.leetcode_username(), "saved-user")
        self.assertEqual(effective.problems_per_minute(), 5.0)

    def test_changes_apply_without_a_restart(self):
        self.assertEqual(effective.problems_per_minute(), 5.0)
        update_config({"problems_per_minute": 2})
        self.assertEqual(effective.problems_per_minute(), 2)
        update_config({"problems_per_minute": None})
        self.assertEqual(effective.problems_per_minute(), 5.0)

    def test_a_broken_config_file_is_a_readable_error(self):
        self.write_raw("{ nope")
        with self.assertRaisesMessage(ConfigError, "not valid JSON"):
            effective.problems_per_minute()
