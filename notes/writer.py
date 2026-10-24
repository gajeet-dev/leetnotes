"""Writes one note into one output folder, following the policy, and keeps its record."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import structlog
from django.utils import timezone

from problems.models import Problem

from .backup import backup_note
from .files import write_atomic
from .hashing import content_hash
from .layout import NotesLayout, ensure_layout, resolve_output_dirs
from .models import NoteRecord
from .planning import WritePlan, plan_write
from .policy import NoteAction
from .state import dir_key, expected_path

logger = structlog.get_logger(__name__)

LEAVE_ALONE = {NoteAction.SKIP_EDITED, NoteAction.UP_TO_DATE}


@dataclass(frozen=True)
class NoteWriteOutcome:
    path: Path
    action: NoteAction | None  # None when the write failed before a decision was made
    wrote: bool = False  # a file was actually written
    backup: Path | None = None  # where the previous version went, if it was backed up
    error: str | None = None


@dataclass
class NoteWriteResult:
    """What happened to one kind of note for one problem, across all output folders."""

    slug: str
    kind: str
    written: list[Path] = field(default_factory=list)
    existing: list[Path] = field(
        default_factory=list
    )  # left as they are: edited by you, or already up to date
    backups: list[Path] = field(
        default_factory=list
    )  # old versions copied aside before replacing
    errors: dict[str, str] = field(default_factory=dict)  # output folder -> error
    outcomes: list[NoteWriteOutcome] = field(
        default_factory=list
    )  # the exact action per folder
    skipped_reason: str | None = None

    def record(self, output_dir: Path, outcome: NoteWriteOutcome) -> None:
        if outcome.error:
            self.errors[str(output_dir)] = outcome.error
        elif outcome.wrote:
            self.written.append(outcome.path)
        else:
            self.existing.append(outcome.path)
        if outcome.backup:
            self.backups.append(outcome.backup)
        self.outcomes.append(outcome)


def write_note(
    problem: Problem,
    kind: str,
    output_dir: Path | str,
    text: str,
    *,
    replace_edited: bool = False,
) -> NoteWriteOutcome:
    """Write one note into one output folder, following the policy.

    Problems with the folder or the disk come back as outcome.error, so one
    bad folder never stops the others.
    """
    output_dir = Path(output_dir).expanduser()
    path = expected_path(problem, kind, output_dir)
    try:
        layout = ensure_layout(output_dir)
        plan = plan_write(
            problem, kind, output_dir, text, replace_edited=replace_edited
        )
        outcome = carry_out(plan, problem, kind, output_dir, layout, text)
    except OSError as exc:
        logger.warning(
            "note_write_failed",
            slug=problem.slug,
            kind=kind,
            dir=str(output_dir),
            error=str(exc),
        )
        return NoteWriteOutcome(path=path, action=None, error=str(exc))

    logger.info(
        "note_handled",
        slug=problem.slug,
        kind=kind,
        action=outcome.action.value,
        path=str(outcome.path),
    )
    return outcome


def carry_out(
    plan: WritePlan,
    problem: Problem,
    kind: str,
    output_dir: Path,
    layout: NotesLayout,
    text: str,
) -> NoteWriteOutcome:
    """Do what the plan says."""
    if plan.action in LEAVE_ALONE:
        return NoteWriteOutcome(plan.path, plan.action)

    if plan.action is NoteAction.ADOPT:
        # The file already holds this text: just start tracking it.
        record_note(problem, kind, output_dir, plan.path, text)
        return NoteWriteOutcome(plan.path, plan.action)

    backup = back_up_if_replacing(
        plan, layout, problem
    )  # before anything is overwritten
    write_atomic(plan.path, text)
    record_note(problem, kind, output_dir, plan.path, text)
    return NoteWriteOutcome(plan.path, plan.action, wrote=True, backup=backup)


def back_up_if_replacing(
    plan: WritePlan, layout: NotesLayout, problem: Problem
) -> Path | None:
    """Copy an edited note aside before it is replaced. If this fails, nothing is overwritten."""
    if plan.action is not NoteAction.BACKUP_AND_OVERWRITE:
        return None
    return backup_note(plan.path, layout.backup_dir, problem.slug)


def record_note(
    problem: Problem, kind: str, output_dir: Path, path: Path, text: str
) -> NoteRecord:
    """Remember what we wrote, so a later edit can be told apart."""
    record, _ = NoteRecord.objects.update_or_create(
        problem=problem,
        kind=kind,
        output_dir=dir_key(output_dir),
        defaults={
            "relative_path": str(path.relative_to(output_dir)),
            "content_hash": content_hash(text),
            "written_at": timezone.now(),
        },
    )
    return record


def write_to_folders(
    problem: Problem,
    kind: str,
    text: str,
    output_dirs: Sequence[Path | str] | None = None,
    *,
    replace_edited: bool = False,
    before_write: Callable[[Path], str | None] | None = None,
) -> NoteWriteResult:
    """Write one rendered note into every output folder (or the configured ones).

    before_write runs first for each folder and returns an error message to
    skip that folder, or None to continue.
    """
    result = NoteWriteResult(problem.slug, kind)
    for output_dir in resolve_output_dirs(output_dirs):
        if before_write and (error := before_write(output_dir)):
            result.errors[str(output_dir)] = error
            continue
        outcome = write_note(
            problem, kind, output_dir, text, replace_edited=replace_edited
        )
        result.record(output_dir, outcome)
    return result
