from django.core.exceptions import ImproperlyConfigured

from prefill.providers.cli import CliProvider
from prefill.providers.defaults import BUILTIN_PROVIDERS
from prefill.providers.registry import get_provider
from userconfig import update_config
from userconfig.tests.helpers import DataDirTestCase


class RegistryTests(DataDirTestCase):
    def test_default_builds_a_cli_provider(self):
        provider = get_provider()
        self.assertIsInstance(provider, CliProvider)
        self.assertEqual((provider.name, provider.model), ("claude", "sonnet"))

    def test_a_saved_model_is_used(self):
        update_config({"providers": {"claude": {"model": "opus"}}})
        self.assertEqual(get_provider("claude").model, "opus")

    def test_unknown_type(self):
        update_config({"providers": {"a": {"type": "carrier-pigeon"}}})
        with self.assertRaisesMessage(
            ImproperlyConfigured, "unknown type 'carrier-pigeon'"
        ):
            get_provider("a")

    def test_bad_option_names_the_provider(self):
        update_config({"providers": {"claude": {"modle": "x"}}})
        with self.assertRaisesMessage(ImproperlyConfigured, 'provider "claude"'):
            get_provider("claude")

    def test_every_builtin_provider_builds_without_running_its_tool(self):
        for name in BUILTIN_PROVIDERS:
            self.assertEqual(get_provider(name).name, name)
