"""A provider that runs a command-line AI tool (fcc-claude, agy, ...)."""

import json
import subprocess
import tempfile
import time
from pathlib import Path

import structlog

from .base import Provider, ProviderError, ProviderResult, ProviderUnavailable

logger = structlog.get_logger(__name__)

PROMPT_PLACEHOLDER = "{prompt}"
MODEL_PLACEHOLDER = "{model}"


class CliProvider(Provider):
    def __init__(
        self,
        name: str,
        *,
        command: list[str],
        model: str = "",
        prompt_via: str = "stdin",
        system_prompt_flag: str | None = None,
        envelope_key: str | None = None,
        timeout: float = 300,
        cwd: Path | None = None,
    ):
        super().__init__(name, model)
        self.command = command
        self.prompt_via = prompt_via
        self.system_prompt_flag = system_prompt_flag
        self.envelope_key = envelope_key
        self.timeout = timeout
        # Run from a neutral folder so the tool can't see this project's files.
        self.cwd = cwd or Path(tempfile.gettempdir())
        self.check_settings()

    # --- building the provider ---

    @classmethod
    def from_settings(cls, name: str, entry: dict) -> "CliProvider":
        options = {key: value for key, value in entry.items() if key != "type"}
        try:
            return cls(name, **options)
        except (
            TypeError,
            ValueError,
        ) as exc:  # unknown option, missing option, bad value
            raise ValueError(f'provider "{name}": {exc}') from exc

    def check_settings(self) -> None:
        if self.prompt_via not in ("stdin", "arg"):
            raise ValueError("prompt_via must be 'stdin' or 'arg'")
        if self.prompt_via == "arg" and PROMPT_PLACEHOLDER not in self.command:
            raise ValueError(
                "prompt_via='arg' needs a '{prompt}' element in the command"
            )

    # --- the main flow ---

    def generate(self, system: str, user: str) -> ProviderResult:
        prompt, extra_args = self.split_prompt(system, user)
        command = self.build_command(prompt, extra_args)
        started = time.monotonic()
        completed = self.run_command(command, prompt)
        text = self.extract_text(completed)
        logger.info(
            "cli_provider_call",
            provider=self.name,
            seconds=round(time.monotonic() - started, 1),
        )
        return ProviderResult(text=self.ensure_text(text), model=self.model)

    # --- step 1: decide what to send ---

    def split_prompt(self, system: str, user: str) -> tuple[str, list[str]]:
        """Return (prompt text, extra args), using the tool's system-prompt flag if it has one."""
        if self.system_prompt_flag:
            return user, [self.system_prompt_flag, system]
        return f"{system}\n\n---\n\n{user}", []

    def build_command(self, prompt: str, extra_args: list[str]) -> list[str]:
        filled = [self.fill_placeholders(part, prompt) for part in self.command]
        return filled + extra_args

    def fill_placeholders(self, part: str, prompt: str) -> str:
        if part == PROMPT_PLACEHOLDER:
            return prompt
        return part.replace(MODEL_PLACEHOLDER, self.model)

    # --- step 2: run the tool ---

    def run_command(
        self, command: list[str], prompt: str
    ) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=self.cwd,
                **self.stdin_options(prompt),
            )
        except FileNotFoundError as exc:
            raise ProviderUnavailable(f"command not found: {command[0]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise ProviderError(
                f"{self.name} timed out after {self.timeout:.0f}s"
            ) from exc

    def stdin_options(self, prompt: str) -> dict:
        if self.prompt_via == "stdin":
            return {"input": prompt}
        return {"stdin": subprocess.DEVNULL}  # never let the tool wait on our terminal

    # --- step 3: get the model's text out of the result ---

    def extract_text(self, completed: subprocess.CompletedProcess) -> str:
        if self.envelope_key:
            return self.read_envelope(completed)
        self.check_exit_code(completed)
        return completed.stdout

    def check_exit_code(self, completed: subprocess.CompletedProcess) -> None:
        if completed.returncode != 0:
            raise ProviderError(
                f"{self.name} exited with {completed.returncode}: "
                f"{completed.stderr.strip()[:500]}"
            )

    def read_envelope(self, completed: subprocess.CompletedProcess) -> str:
        """Pull the reply out of the tool's JSON envelope.

        Claude Code exits 1 and still prints an envelope when the API call
        fails, so the envelope is read before the exit code is judged.
        """
        envelope = self.parse_envelope_json(completed)
        self.raise_if_envelope_failed(envelope)
        return self.envelope_text(envelope)

    def parse_envelope_json(self, completed: subprocess.CompletedProcess) -> dict:
        try:
            envelope = json.loads(completed.stdout)
        except json.JSONDecodeError:
            detail = (completed.stderr or completed.stdout).strip()[:500]
            raise ProviderError(
                f"{self.name} exited with {completed.returncode} "
                f"and no JSON envelope: {detail}"
            ) from None
        if not isinstance(envelope, dict):
            raise ProviderError(f"{self.name}: unexpected envelope shape")
        return envelope

    def raise_if_envelope_failed(self, envelope: dict) -> None:
        reason = envelope.get("terminal_reason")
        if envelope.get("is_error") or reason not in (None, "completed"):
            detail = envelope.get(self.envelope_key) or reason or "unknown error"
            raise ProviderUnavailable(f"{self.name} reported an error: {detail}")

    def envelope_text(self, envelope: dict) -> str:
        text = envelope.get(self.envelope_key)
        if not isinstance(text, str):
            raise ProviderError(
                f"{self.name}: envelope has no '{self.envelope_key}' text"
            )
        return text
