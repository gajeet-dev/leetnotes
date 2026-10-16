from typing import Any

from ..dto import ProblemDTO, SolvedProblemDTO
from .base import build


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


def map_solved_problem(raw: dict[str, Any]) -> SolvedProblemDTO:
    stat = raw.get("stat") or {}
    return build(
        SolvedProblemDTO,
        leetcode_id=stat.get("frontend_question_id"),
        slug=stat.get("question__title_slug"),
        title=stat.get("question__title"),
    )
