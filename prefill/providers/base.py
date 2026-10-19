"""The shared contract every AI provider follows."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


class ProviderError(Exception):
    """One generation failed. Other problems may still work."""


class ProviderUnavailable(ProviderError):
    """Tool missing, logged out, or out of quota: every later call would fail too."""


@dataclass(frozen=True)
class ProviderResult:
    text: str
    model: str = ""


class Provider(ABC):
    """Turns a system prompt and a user prompt into model text.

    Subclasses (CliProvider, a future ApiProvider, ...) implement how.
    The rest of the app talks only to this interface.
    """

    def __init__(self, name: str, model: str = ""):
        self.name = name
        self.model = model

    @classmethod
    @abstractmethod
    def from_settings(cls, name: str, entry: dict) -> "Provider":
        """Build the provider from its entry in ai_providers.json."""

    @abstractmethod
    def generate(self, system: str, user: str) -> ProviderResult:
        """Run one generation. Raise ProviderError or ProviderUnavailable on failure."""

    def ensure_text(self, text: str) -> str:
        """Shared check: a provider that returns nothing has failed."""
        if not text.strip():
            raise ProviderError(f"{self.name} returned no output")
        return text
