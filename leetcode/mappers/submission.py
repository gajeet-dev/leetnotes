from typing import Any

from ..dto import RecentAcDTO, SubmissionDetailDTO, SubmissionSummaryDTO
from .base import build


def map_submission_summary(raw: dict[str, Any]) -> SubmissionSummaryDTO:
    return build(
        SubmissionSummaryDTO,
        id=raw.get("id"),
        status=raw.get("statusDisplay"),
        lang=raw.get("lang"),
        timestamp=raw.get("timestamp"),
    )


def map_recent_ac(raw: dict[str, Any]) -> RecentAcDTO:
    raw_id = raw.get("id")
    sub_id = int(raw_id) if raw_id is not None else None
    return build(
        RecentAcDTO,
        id=sub_id,
        slug=raw.get("titleSlug"),
        title=raw.get("title"),
        timestamp=raw.get("timestamp"),
    )


def map_submission_detail(raw: dict[str, Any]) -> SubmissionDetailDTO:
    return build(
        SubmissionDetailDTO,
        lang=(raw.get("lang") or {}).get("name"),
        code=raw.get("code"),
        submission_date=raw.get("timestamp"),
    )
