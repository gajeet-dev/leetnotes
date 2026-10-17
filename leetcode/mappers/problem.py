from typing import Any

from ..dto import ProblemDTO, DiscoveredProblemDTO
from .base import build

_DIFFICULTY_BY_LEVEL: dict[int, str] = {1: "Easy", 2: "Medium", 3: "Hard"}


def _difficulty(raw: dict[str, Any]) -> str | None:
    level = (raw.get("difficulty") or {}).get("level")
    if level is None:
        return None
    return _DIFFICULTY_BY_LEVEL.get(level)


def map_problem(raw: dict[str, Any]) -> ProblemDTO:
    return build(
        ProblemDTO,
        slug=raw.get("titleSlug"),
        leetcode_id=raw.get("questionFrontendId"),
        title=raw.get("title"),
        difficulty=raw.get("difficulty"),
        category=raw.get("categoryTitle"),
        tags=raw.get("topicTags") or [],
        raw_question_html=raw.get("content") or None,
    )


def map_discovered_problem(raw: dict[str, Any]) -> DiscoveredProblemDTO:
    stat = raw.get("stat") or {}
    return build(
        DiscoveredProblemDTO,
        leetcode_id=stat.get("frontend_question_id"),
        solved=raw.get("status") == "ac",
        paid_only=bool(raw.get("paid_only")),
        slug=stat.get("question__title_slug"),
        title=stat.get("question__title"),
        difficulty=_difficulty(raw),
    )
