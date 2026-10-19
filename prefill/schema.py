import re
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints

SCHEMA_VERSION = 1
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")


def _to_str_list(value):
    """Accept a bare string; drop empty items and any bullet markers the model added."""
    if isinstance(value, str):
        value = [value]
    if isinstance(value, (list, tuple)):
        cleaned = (_BULLET.sub("", str(item)).strip() for item in value)
        return [text for text in cleaned if text]
    return value  # anything else: let pydantic report the type error


def _single_line(value):
    """One line, and no pipes (they would break the comparison table)."""
    if isinstance(value, str):
        return " ".join(value.replace("|", "/").split())
    return value


def _strip_ticks(value):
    """The template adds the backticks around complexity values itself."""
    return value.strip().strip("`").strip() if isinstance(value, str) else value


StrList = Annotated[list[str], BeforeValidator(_to_str_list)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Line = Annotated[
    str,
    BeforeValidator(_single_line),
    StringConstraints(strip_whitespace=True, min_length=1),
]
Complexity = Annotated[
    str,
    BeforeValidator(_strip_ticks),
    StringConstraints(strip_whitespace=True, min_length=1),
]


class Approach(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: Line
    code_ref: str | None = None
    core_idea: StrList
    trap: StrList
    complexity_time: Complexity
    complexity_space: Complexity
    pick_when: Annotated[str, BeforeValidator(_single_line)] | None = None


class SolutionNote(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: int = SCHEMA_VERSION
    problem_summary: Line
    recognition_clue: StrList
    approaches: list[Approach] = Field(min_length=1)
    takeaway: StrList


def json_schema() -> dict:
    """The JSON Schema to embed in the prompt, so prompt and validator can't drift."""
    return SolutionNote.model_json_schema()
