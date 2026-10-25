import json
import os
import stat
import unittest

from django.test import SimpleTestCase

from userconfig.credentials import (
    LeetCodeCredentials,
    clean_cookie_value,
    clear_credentials,
    credentials_view,
    load_credentials,
    mask_secret,
    save_credentials,
    update_credentials,
)
from userconfig.errors import ConfigError
from userconfig.paths import secrets_path
from userconfig.tests.helpers import DataDirTestCase

SESSION = "S3cr3t-Session-Value-ABCDEFGH1234"
CSRF = "csrf-Token-Value-WXYZ98765432"


class CleanCookieValueTests(SimpleTestCase):
    def clean(self, raw: str) -> str:
        return clean_cookie_value(raw, "LEETCODE_SESSION")

    def test_plain_value_is_unchanged(self):
        self.assertEqual(self.clean("abc.def"), "abc.def")

    def test_name_prefix_semicolon_and_quotes_are_removed(self):
        self.assertEqual(self.clean("  LEETCODE_SESSION=abc.def;  "), "abc.def")
        self.assertEqual(self.clean('"abc.def"'), "abc.def")

    def test_blank_stays_blank(self):
        self.assertEqual(self.clean("   "), "")

    def test_two_cookies_pasted_together_are_rejected(self):
        for raw in ("abc; csrftoken=def", "abc;csrftoken=def", "abc def"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                self.clean(raw)


class MaskTests(SimpleTestCase):
    def test_empty_short_and_long(self):
        self.assertEqual(mask_secret(""), "")
        self.assertEqual(mask_secret("short"), "•" * 8)
        self.assertEqual(mask_secret(SESSION), "•" * 8 + "1234")

    def test_the_hint_does_not_contain_the_secret(self):
        self.assertNotIn(SESSION[:10], mask_secret(SESSION))


class SecrecyTests(SimpleTestCase):
    def test_repr_and_str_do_not_leak(self):
        credentials = LeetCodeCredentials(session=SESSION, csrf_token=CSRF)
        for text in (repr(credentials), str(credentials)):
            self.assertNotIn(SESSION, text)
            self.assertNotIn(CSRF, text)

    def test_the_view_never_contains_the_values(self):
        view = credentials_view(LeetCodeCredentials(session=SESSION, csrf_token=CSRF))
        self.assertTrue(view.configured)
        self.assertNotIn(SESSION, repr(view))
        self.assertNotIn(CSRF, repr(view))


class StoredCredentialsTests(DataDirTestCase):
    def test_nothing_stored_means_empty_and_creates_nothing(self):
        self.assertFalse(load_credentials().is_complete)
        self.assertFalse(self.data_dir.exists())

    def test_update_saves_and_round_trips(self):
        update_credentials(session=SESSION, csrf_token=CSRF)
        loaded = load_credentials()
        self.assertEqual((loaded.session, loaded.csrf_token), (SESSION, CSRF))
        self.assertIsNotNone(loaded.updated_at)

    def test_a_blank_field_keeps_the_stored_value(self):
        update_credentials(session=SESSION, csrf_token=CSRF)
        update_credentials(session="", csrf_token="new-" + CSRF)
        loaded = load_credentials()
        self.assertEqual(loaded.session, SESSION)
        self.assertEqual(loaded.csrf_token, "new-" + CSRF)

    def test_no_changes_leaves_the_file_alone(self):
        update_credentials(session=SESSION, csrf_token=CSRF)
        before = secrets_path().read_text()
        update_credentials(session=None, csrf_token="  ")
        self.assertEqual(secrets_path().read_text(), before)

    def test_an_invalid_cookie_is_rejected_without_leaking_it(self):
        with self.assertRaises(ConfigError) as caught:
            update_credentials(session="LEAKME one LEAKME_TWO")
        self.assertNotIn("LEAKME", str(caught.exception))
        self.assertFalse(secrets_path().exists())

    def test_one_cookie_alone_is_not_configured(self):
        update_credentials(session=SESSION)
        self.assertFalse(credentials_view().configured)

    def test_clear_removes_the_file(self):
        update_credentials(session=SESSION, csrf_token=CSRF)
        self.assertTrue(clear_credentials())
        self.assertFalse(clear_credentials())
        self.assertFalse(load_credentials().is_complete)

    def test_an_unreadable_file_is_a_readable_error(self):
        self.write_raw("")
        secrets_path().write_text("{ nope")
        with self.assertRaisesMessage(ConfigError, "not valid JSON"):
            load_credentials()


@unittest.skipUnless(os.name == "posix", "permission bits are POSIX-only")
class PermissionTests(DataDirTestCase):
    def mode(self, path) -> int:
        return stat.S_IMODE(path.stat().st_mode)

    def test_the_file_and_the_folder_are_private(self):
        save_credentials(LeetCodeCredentials(session=SESSION, csrf_token=CSRF))
        self.assertEqual(self.mode(secrets_path()), 0o600)
        self.assertEqual(self.mode(self.data_dir), 0o700)

    def test_a_loose_file_is_tightened_on_load(self):
        save_credentials(LeetCodeCredentials(session=SESSION, csrf_token=CSRF))
        os.chmod(secrets_path(), 0o644)
        load_credentials()
        self.assertEqual(self.mode(secrets_path()), 0o600)

    def test_no_temp_file_is_left_behind(self):
        save_credentials(LeetCodeCredentials(session=SESSION, csrf_token=CSRF))
        self.assertEqual(
            [path.name for path in self.data_dir.iterdir()], ["secrets.json"]
        )
