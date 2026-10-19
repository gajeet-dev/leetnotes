import json
import tempfile
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from prefill.providers.config import provider_entry


def write_file(folder: str, content) -> Path:
    path = Path(folder) / "providers.json"
    path.write_text(content if isinstance(content, str) else json.dumps(content))
    return path


GOOD = {
    "default": "a",
    "providers": {
        "a": {"type": "cli", "model": "m1"},
        "b": {"type": "cli"},
    },
}


class ProviderConfigTests(SimpleTestCase):
    def check(self, content, **kwargs):
        with tempfile.TemporaryDirectory() as folder:
            path = write_file(folder, content)
            with override_settings(PREFILL_PROVIDERS_FILE=path):
                return provider_entry(**kwargs)

    def test_default_provider(self):
        self.assertEqual(self.check(GOOD), ("a", {"type": "cli", "model": "m1"}))

    def test_named_provider(self):
        self.assertEqual(self.check(GOOD, name="b")[0], "b")

    def test_unknown_provider_lists_known_names(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "Known: a, b"):
            self.check(GOOD, name="zzz")

    def test_invalid_json(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "not valid JSON"):
            self.check("{ nope")

    def test_missing_file(self):
        with override_settings(PREFILL_PROVIDERS_FILE=Path("/no/such/file.json")):
            with self.assertRaisesMessage(ImproperlyConfigured, "not found"):
                provider_entry()

    def test_provider_without_type(self):
        with self.assertRaisesMessage(ImproperlyConfigured, 'needs a "type"'):
            self.check({"default": "a", "providers": {"a": {"model": "m"}}})

    def test_default_must_exist(self):
        with self.assertRaisesMessage(ImproperlyConfigured, '"default" must be one of'):
            self.check({"default": "x", "providers": {"a": {"type": "cli"}}})

    def test_edits_apply_without_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = write_file(folder, GOOD)
            with override_settings(PREFILL_PROVIDERS_FILE=path):
                self.assertEqual(provider_entry()[1]["model"], "m1")
                write_file(
                    folder, {**GOOD, "providers": {"a": {"type": "cli", "model": "m2"}}}
                )
                self.assertEqual(provider_entry()[1]["model"], "m2")
