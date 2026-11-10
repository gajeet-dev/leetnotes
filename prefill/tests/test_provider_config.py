from django.core.exceptions import ImproperlyConfigured

from prefill.providers.config import (
    all_providers,
    default_provider_name,
    provider_entry,
    provider_names,
)
from prefill.providers.defaults import BUILTIN_PROVIDERS, load_builtin_providers
from userconfig import update_config
from userconfig.paths import ai_providers_path
from userconfig.tests.helpers import DataDirTestCase


class ProviderConfigTests(DataDirTestCase):
    def test_builtins_are_available_with_nothing_saved(self):
        self.assertEqual(provider_names(), ["manual", "claude", "agy"])
        self.assertEqual(default_provider_name(), "manual")
        self.assertEqual(provider_entry("agy")[1], BUILTIN_PROVIDERS["agy"])

    def test_default_effort_is_medium_for_cli_providers(self):
        self.assertEqual(BUILTIN_PROVIDERS["claude"]["effort"], "medium")
        self.assertEqual(BUILTIN_PROVIDERS["agy"]["effort"], "medium")

    def test_load_builtin_providers_from_single_source_of_truth(self):
        # 1. Loading from root ai_providers.json
        loaded = load_builtin_providers(ai_providers_path())
        self.assertIn("claude", loaded)
        self.assertIn("agy", loaded)
        self.assertEqual(loaded["claude"]["effort"], "medium")
        self.assertEqual(loaded["agy"]["effort"], "medium")

        # 2. Loading from non-existent file raises ImproperlyConfigured
        missing_path = ai_providers_path().parent / "non_existent_file.json"
        with self.assertRaisesMessage(ImproperlyConfigured, "Missing AI providers file"):
            load_builtin_providers(missing_path)

        # 3. Loading invalid JSON raises ImproperlyConfigured
        bad_json_path = self.data_dir / "bad_providers.json"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        bad_json_path.write_text("{ not json")
        with self.assertRaisesMessage(ImproperlyConfigured, "Invalid JSON in"):
            load_builtin_providers(bad_json_path)

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
        self.assertEqual(provider_names(), ["manual", "claude", "agy", "mine"])

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
        with self.assertRaisesMessage(ImproperlyConfigured, "Known: manual, claude, agy"):
            provider_entry("zzz")

    def test_a_broken_config_file_is_an_improperly_configured_error(self):
        self.write_raw("{ nope")
        with self.assertRaisesMessage(ImproperlyConfigured, "not valid JSON"):
            provider_names()
