from datetime import datetime

from pydantic import BaseModel


class TagDTO(BaseModel):
    name: str
    slug: str


class ProblemDTO(BaseModel):
    slug: str
    leetcode_id: int
    title: str
    difficulty: str | None = None
    category: str | None = None
    tags: list[TagDTO] = []
    raw_question_html: str | None = None  # None for locked/premium problems


class SubmissionSummaryDTO(BaseModel):
    id: int
    status: str | None = None
    lang: str
    timestamp: datetime


class RecentAcDTO(BaseModel):
    slug: str
    title: str
    timestamp: datetime


class SubmissionDetailDTO(BaseModel):
    lang: str
    code: str
    submission_date: datetime


class DiscoveredProblemDTO(BaseModel):
    slug: str
    title: str
    leetcode_id: int | None = None
    difficulty: str | None = None
    solved: bool
    paid_only: bool = False
