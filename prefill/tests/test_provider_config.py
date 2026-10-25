from django.core.exceptions import ImproperlyConfigured

from prefill.providers.config import (
    all_providers,
    default_provider_name,
    provider_entry,
    provider_names,
)
from prefill.providers.defaults import BUILTIN_PROVIDERS
from userconfig import update_config
from userconfig.tests.helpers import DataDirTestCase


class ProviderConfigTests(DataDirTestCase):
    def test_builtins_are_available_with_nothing_saved(self):
        self.assertEqual(provider_names(), ["claude", "agy"])
        self.assertEqual(default_provider_name(), "claude")
        self.assertEqual(provider_entry("agy")[1], BUILTIN_PROVIDERS["agy"])

    def test_saved_options_override_builtin_ones_field_by_field(self):
        update_config({"providers": {"claude": {"model": "opus"}}})
        entry = provider_entry("claude")[1]
        self.assertEqual(entry["model"], "opus")
        self.assertEqual(entry["envelope_key"], "result")  # untouched options stay

    def test_overrides_never_change_the_builtin_definitions(self):
        update_config({"providers": {"claude": {"model": "opus"}}})
        provider_entry("claude")
        self.assertEqual(BUILTIN_PROVIDERS["claude"]["model"], "sonnet")

    def test_a_custom_provider_can_be_added(self):
        update_config({"providers": {"mine": {"type": "cli", "command": ["mytool"]}}})
        self.assertEqual(provider_names(), ["claude", "agy", "mine"])

    def test_a_custom_provider_needs_a_type(self):
        update_config({"providers": {"mine": {"command": ["mytool"]}}})
        with self.assertRaisesMessage(
            ImproperlyConfigured, 'provider "mine" needs a "type"'
        ):
            all_providers()

    def test_the_default_can_be_changed(self):
        update_config({"default_provider": "agy"})
        self.assertEqual(provider_entry()[0], "agy")

    def test_the_default_must_exist(self):
        update_config({"default_provider": "ghost"})
        with self.assertRaisesMessage(ImproperlyConfigured, "ghost"):
            default_provider_name()

    def test_unknown_provider_lists_known_names(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "Known: claude, agy"):
            provider_entry("zzz")

    def test_a_broken_config_file_is_an_improperly_configured_error(self):
        self.write_raw("{ nope")
        with self.assertRaisesMessage(ImproperlyConfigured, "not valid JSON"):
            provider_names()
