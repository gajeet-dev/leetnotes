"""A provider for manual web-chat paste workflows."""

from .base import Provider, ProviderError, ProviderResult


class ManualProvider(Provider):
    """Provider for pasting AI responses manually via the web UI."""

    @classmethod
    def from_settings(cls, name: str, entry: dict) -> "ManualProvider":
        return cls(name=name, model=entry.get("model", "manual"))

    def generate(self, system: str, user: str) -> ProviderResult:
        raise ProviderError(
            f"{self.name}: manual provider requires pasting input via the web UI."
        )
