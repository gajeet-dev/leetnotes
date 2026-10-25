import json
import os
import stat
import unittest

from userconfig import AppConfig, ConfigError, load_config, save_config, update_config
from userconfig.files import write_text_atomic
from userconfig.paths import config_path
from userconfig.tests.helpers import DataDirTestCase


class LoadConfigTests(DataDirTestCase):
    def test_defaults_when_nothing_is_saved(self):
        self.assertEqual(load_config(), AppConfig())
        self.assertFalse(self.data_dir.exists())  # loading never creates anything

    def test_save_creates_the_folder_and_round_trips(self):
        save_config(
            AppConfig(
                output_dirs=["/vault"],
                leetcode_username="ajeet",
                time_zone="Asia/Kolkata",
            )
        )
        loaded = load_config()
        self.assertEqual(loaded.output_dirs, ["/vault"])
        self.assertEqual(loaded.time_zone, "Asia/Kolkata")

    def test_saved_file_is_readable_json(self):
        save_config(AppConfig())
        text = config_path().read_text()
        self.assertEqual(json.loads(text)["version"], 1)
        self.assertTrue(text.endswith("\n"))

    def test_invalid_json_is_a_readable_error(self):
        self.write_raw("{ nope")
        with self.assertRaisesMessage(ConfigError, "not valid JSON"):
            load_config()

    def test_unknown_time_zone_is_rejected(self):
        self.write_raw(json.dumps({"time_zone": "Mars/Olympus"}))
        with self.assertRaisesMessage(ConfigError, "time_zone"):
            load_config()

    def test_pacing_must_be_positive(self):
        self.write_raw(json.dumps({"problems_per_minute": 0}))
        with self.assertRaisesMessage(ConfigError, "problems_per_minute"):
            load_config()

    def test_unknown_keys_are_ignored_so_older_or_newer_files_still_load(self):
        self.write_raw(
            json.dumps({"leetcode_username": "ajeet", "future_option": True})
        )
        self.assertEqual(load_config().leetcode_username, "ajeet")

    def test_output_dirs_are_cleaned(self):
        self.write_raw(json.dumps({"output_dirs": [" /a ", "", "/a", "/b"]}))
        self.assertEqual(load_config().output_dirs, ["/a", "/b"])

    def test_blank_values_mean_not_set(self):
        self.write_raw(
            json.dumps({"output_dirs": ["", " "], "leetcode_username": "  "})
        )
        loaded = load_config()
        self.assertIsNone(loaded.output_dirs)
        self.assertIsNone(loaded.leetcode_username)

    def test_saving_none_clears_a_setting(self):
        save_config(AppConfig(leetcode_username="ajeet"))
        self.assertIsNone(update_config({"leetcode_username": None}).leetcode_username)


class UpdateConfigTests(DataDirTestCase):
    def test_update_changes_only_what_is_given_and_saves(self):
        save_config(AppConfig(leetcode_username="ajeet", output_dirs=["/vault"]))
        updated = update_config({"problems_per_minute": 3})
        self.assertEqual(
            (updated.problems_per_minute, updated.leetcode_username), (3, "ajeet")
        )
        self.assertEqual(load_config().problems_per_minute, 3)

    def test_an_invalid_update_is_rejected_and_nothing_is_saved(self):
        save_config(AppConfig(time_zone="Asia/Kolkata"))
        with self.assertRaises(ConfigError):
            update_config({"time_zone": "nowhere"})
        self.assertEqual(load_config().time_zone, "Asia/Kolkata")


class WriteTextAtomicTests(DataDirTestCase):
    def test_no_temp_file_is_left_behind(self):
        save_config(AppConfig())
        self.assertEqual(
            [path.name for path in self.data_dir.iterdir()], ["config.json"]
        )

    @unittest.skipUnless(os.name == "posix", "permission bits are POSIX-only")
    def test_private_mode_is_applied(self):
        self.data_dir.mkdir(parents=True)
        path = self.data_dir / "secret.txt"
        write_text_atomic(path, "x", mode=0o600)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
