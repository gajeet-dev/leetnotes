import json
from collections.abc import Iterator

from pydantic import ValidationError

from .schema import SolutionNote


class ParseError(Exception):
    """The text held no valid SolutionNote. str(exc) is readable in the admin."""


def _json_objects(text: str) -> Iterator[dict]:
    """Yield every top-level JSON object found anywhere in the text.

    Handles bare JSON, ```json fences, and prose before or after, without
    regexes. Braces inside strings (code!) are fine because the real JSON
    decoder does the scanning.
    """
    decoder = json.JSONDecoder()
    pos = 0
    while (start := text.find("{", pos)) != -1:
        try:
            value, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            pos = start + 1
            continue
        if isinstance(value, dict):
            yield value
        pos = end  # skip past it, so nested objects aren't yielded separately


def _format_errors(exc: ValidationError, limit: int = 10) -> str:
    lines = [
        f"{'.'.join(str(p) for p in err['loc']) or '(root)'}: {err['msg']}"
        for err in exc.errors()
    ]
    if len(lines) > limit:
        lines = lines[:limit] + [f"... and {len(lines) - limit} more"]
    return "\n".join(lines)


def parse_solution(text: str) -> SolutionNote:
    """Turn model output into a validated SolutionNote, or raise ParseError."""
    first_error: ValidationError | None = None

    for candidate in _json_objects(text):
        try:
            return SolutionNote.model_validate(candidate)
        except ValidationError as exc:
            first_error = first_error or exc

    if first_error is not None:
        raise ParseError(_format_errors(first_error)) from first_error
    raise ParseError("No JSON object found in the text.")
