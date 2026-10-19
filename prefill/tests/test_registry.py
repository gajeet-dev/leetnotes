import json
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from prefill.providers.cli import CliProvider
from prefill.providers.registry import get_provider


def provider_file(folder: str, providers: dict, default: str = "a") -> Path:
    path = Path(folder) / "providers.json"
    path.write_text(json.dumps({"default": default, "providers": providers}))
    return path


class RegistryTests(SimpleTestCase):
    def get(self, providers, name=None):
        with tempfile.TemporaryDirectory() as folder:
            with override_settings(
                PREFILL_PROVIDERS_FILE=provider_file(folder, providers)
            ):
                return get_provider(name)

    def test_default_builds_a_cli_provider(self):
        provider = self.get({"a": {"type": "cli", "command": ["x"], "model": "m"}})
        self.assertIsInstance(provider, CliProvider)
        self.assertEqual((provider.name, provider.model), ("a", "m"))

    def test_unknown_type(self):
        with self.assertRaisesMessage(
            ImproperlyConfigured, "unknown type 'carrier-pigeon'"
        ):
            self.get({"a": {"type": "carrier-pigeon"}})

    def test_bad_option_names_the_provider(self):
        with self.assertRaisesMessage(ImproperlyConfigured, 'provider "a"'):
            self.get({"a": {"type": "cli", "command": ["x"], "modle": "m"}})

    def test_shipped_file_builds_every_provider(self):
        shipped = Path(settings.BASE_DIR) / "ai_providers.json"
        with override_settings(PREFILL_PROVIDERS_FILE=shipped):
            for name in ("claude", "agy"):
                self.assertEqual(get_provider(name).name, name)
