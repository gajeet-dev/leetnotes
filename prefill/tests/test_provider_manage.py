from django.core.exceptions import ImproperlyConfigured

from prefill.providers.config import default_provider_name, provider_names
from prefill.providers.manage import (
    change_provider,
    reset_provider,
    set_default_provider,
)
from prefill.providers.registry import get_provider
from userconfig import load_config
from userconfig.tests.helpers import DataDirTestCase

CUSTOM = {"type": "cli", "command": ["mytool"]}


class ChangeProviderTests(DataDirTestCase):
    def test_a_change_is_used_and_only_the_change_is_stored(self):
        change_provider("claude", {"model": "opus"})
        self.assertEqual(get_provider("claude").model, "opus")
        self.assertEqual(load_config().providers, {"claude": {"model": "opus"}})

    def test_changes_accumulate(self):
        change_provider("claude", {"model": "opus"})
        change_provider("claude", {"timeout": 120})
        self.assertEqual(
            load_config().providers["claude"], {"model": "opus", "timeout": 120}
        )

    def test_a_bad_option_is_rejected_and_nothing_is_saved(self):
        with self.assertRaises(ImproperlyConfigured):
            change_provider("claude", {"modle": "x"})
        self.assertEqual(load_config().providers, {})

    def test_a_new_provider_needs_a_type(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "needs a"):
            change_provider("mine", {"command": ["mytool"]})

    def test_a_custom_provider_can_be_added(self):
        change_provider("mine", CUSTOM)
        self.assertEqual(get_provider("mine").name, "mine")


class ResetAndDefaultTests(DataDirTestCase):
    def test_reset_restores_the_builtin_definition(self):
        change_provider("claude", {"model": "opus"})
        reset_provider("claude")
        self.assertEqual(get_provider("claude").model, "sonnet")

    def test_resetting_a_custom_provider_deletes_it_and_its_default_status(self):
        change_provider("mine", CUSTOM)
        set_default_provider("mine")
        reset_provider("mine")
        self.assertEqual(default_provider_name(), "claude")
        self.assertNotIn("mine", provider_names())

    def test_the_default_can_be_set_and_cleared(self):
        set_default_provider("agy")
        self.assertEqual(get_provider().name, "agy")
        set_default_provider(None)
        self.assertEqual(get_provider().name, "claude")

    def test_the_default_must_exist(self):
        with self.assertRaises(ImproperlyConfigured):
            set_default_provider("ghost")
