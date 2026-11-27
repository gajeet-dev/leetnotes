from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from problems.models import Problem
from ui.helpers.problems import DEFAULT_PER_PAGE, PER_PAGE_PRESETS

PerPagePreset = Literal[25, 50, 100, 200, 250]


class NoteStatus(StrEnum):
    NO_NOTE = "no_note"
    NOTE_SAVED = "note_saved"
    ACTION_NEEDED = "action_needed"
    QUEUED = "queued"
    RUNNING = "running"


PROBLEM_NOTE_STATUSES: tuple[NoteStatus, ...] = (
    NoteStatus.NOTE_SAVED,
    NoteStatus.NO_NOTE,
)

CATALOG_NOTE_STATUSES: tuple[NoteStatus, ...] = (
    NoteStatus.RUNNING,
    NoteStatus.QUEUED,
    NoteStatus.ACTION_NEEDED,
    NoteStatus.NOTE_SAVED,
    NoteStatus.NO_NOTE,
)


@dataclass(frozen=True)
class NoteStatusSpec:
    status: NoteStatus
    label: str
    badge_class: str
    icon: str


NOTE_STATUS_SPECS: dict[NoteStatus, NoteStatusSpec] = {
    NoteStatus.RUNNING: NoteStatusSpec(
        status=NoteStatus.RUNNING,
        label="Running",
        badge_class="bg-blue-50 text-blue-700 border-blue-200 dark:bg-blue-950/80 dark:text-blue-300 dark:border-blue-800 animate-pulse",
        icon="loader-circle",
    ),
    NoteStatus.QUEUED: NoteStatusSpec(
        status=NoteStatus.QUEUED,
        label="Queued",
        badge_class="bg-zinc-100 text-zinc-700 border-zinc-200 dark:bg-zinc-800 dark:text-zinc-300 dark:border-zinc-700",
        icon="clock",
    ),
    NoteStatus.ACTION_NEEDED: NoteStatusSpec(
        status=NoteStatus.ACTION_NEEDED,
        label="Action Needed",
        badge_class="bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-950/80 dark:text-amber-300 dark:border-amber-800",
        icon="circle-alert",
    ),
    NoteStatus.NOTE_SAVED: NoteStatusSpec(
        status=NoteStatus.NOTE_SAVED,
        label="Note Saved",
        badge_class="bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950/80 dark:text-emerald-300 dark:border-emerald-800/60",
        icon="circle-check",
    ),
    NoteStatus.NO_NOTE: NoteStatusSpec(
        status=NoteStatus.NO_NOTE,
        label="No Note",
        badge_class="bg-zinc-100 text-zinc-500 border-zinc-200/70 dark:bg-zinc-800 dark:text-zinc-400 dark:border-zinc-700/60",
        icon="",
    ),
}


class ProblemNoteStatusDTO(BaseModel):
    problem_id: int
    slug: str
    leetcode_id: int | None = None
    note_status: NoteStatus
    label: str
    badge_class: str
    icon: str = ""
    note_saved: bool
    pipeline_status: str | None = None
    pipeline_item_id: int | None = None
    input_kind: str = ""


class ProblemCatalogDTO(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    q: str = ""
    difficulty: Problem.Difficulty | None = None
    status: str = ""

    page: int = Field(default=1, ge=1)
    per_page: PerPagePreset = DEFAULT_PER_PAGE

    @field_validator("difficulty", mode="before")
    @classmethod
    def normalize_difficulty(cls, v: Any) -> Any:
        if not v:
            return None
        if isinstance(v, str):
            clean = v.strip().lower()
            for choice in Problem.Difficulty:
                if choice.value.lower() == clean:
                    return choice
        return v

    @field_validator("page", mode="before")
    @classmethod
    def normalize_page(cls, v: Any) -> Any:
        if v == "" or v is None:
            return 1
        return v

    @field_validator("per_page", mode="before")
    @classmethod
    def normalize_per_page(cls, v: Any) -> int:
        if v == "" or v is None:
            return DEFAULT_PER_PAGE
        try:
            val = int(v)
        except ValueError, TypeError:
            return DEFAULT_PER_PAGE
        return val if val in PER_PAGE_PRESETS else DEFAULT_PER_PAGE
