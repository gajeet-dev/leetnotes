import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from problems.models import Problem

from .schema import json_schema

PROMPT_DIR = Path(__file__).parent / "prompts"

_env = Environment(
    loader=FileSystemLoader(PROMPT_DIR),
    autoescape=False,
    undefined=StrictUndefined,
    keep_trailing_newline=True,
    trim_blocks=True,
    lstrip_blocks=True,
)

_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")


class PromptInputError(Exception):
    """The problem isn't ready to be turned into a prompt."""


@dataclass(frozen=True)
class Prompt:
    system: str
    user: str
    version: str  # changes automatically when either prompt file changes
    input_hash: str  # changes when the problem text or submitted code changes

    @property
    def combined(self) -> str:
        """System + user as one text: for tools without a system-prompt flag,
        and for pasting into a web chat."""
        return f"{self.system}\n\n---\n\n{self.user}"


def _template_source(name: str) -> str:
    return _env.loader.get_source(_env, name)[0]  # type: ignore[union-attr]


def build_prompt(problem: Problem) -> Prompt:
    statement = problem.content_remote_markdown
    if not statement:
        raise PromptInputError("problem content has not been synced yet")

    submission = problem.submissions.order_by("-submission_date").first()
    if submission is None:
        raise PromptInputError("no stored submission for this problem")

    statement = _IMAGE.sub("[image omitted]", statement)
    code = submission.code.rstrip()

    system = _env.get_template("system.md.j2").render(
        schema=json.dumps(json_schema(), indent=2)
    )
    user = _env.get_template("user.md.j2").render(
        id=problem.leetcode_id,
        title=problem.title or problem.slug,
        difficulty=problem.difficulty or "unknown",
        tags=", ".join(t.get("name", "") for t in problem.tags or []) or "none",
        statement=statement,
        lang=submission.lang,
        code=code,
    )

    sources = _template_source("system.md.j2") + _template_source("user.md.j2")
    return Prompt(
        system=system,
        user=user,
        version=hashlib.sha256(sources.encode()).hexdigest()[:8],
        input_hash=hashlib.sha256(
            "\0".join([statement, submission.lang, code]).encode()
        ).hexdigest(),
    )
