from django.test import SimpleTestCase

from prefill.providers.base import Provider, ProviderError, ProviderResult


class EchoProvider(Provider):
    @classmethod
    def from_settings(cls, name, entry):
        return cls(name, entry.get("model", ""))

    def generate(self, system, user):
        return ProviderResult(self.ensure_text(user), self.model)


class ProviderBaseTests(SimpleTestCase):
    def test_cannot_instantiate_the_abstract_class(self):
        with self.assertRaises(TypeError):
            Provider("x")

    def test_subclass_must_implement_both_methods(self):
        class Half(Provider):
            def generate(self, system, user): ...

        with self.assertRaises(TypeError):
            Half("x")

    def test_from_settings_builds_a_provider(self):
        provider = EchoProvider.from_settings("echo", {"model": "m1"})
        self.assertEqual((provider.name, provider.model), ("echo", "m1"))

    def test_ensure_text_rejects_blank_output(self):
        with self.assertRaisesMessage(ProviderError, "returned no output"):
            EchoProvider("echo").generate("s", "   ")
