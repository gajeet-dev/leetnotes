import shutil
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from problems.models import Problem, SyncTask

from .context import build_problem_context
from .files import write_atomic
from .layout import ensure_layout, resolve_output_dirs
from .naming import problem_note_filename
from .rendering import render_template

logger = structlog.get_logger(__name__)


@dataclass
class NoteResult:
    slug: str
    paths: list[Path] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)  # output dir -> error
    skipped_reason: str | None = None


def render_problem_note(problem: Problem) -> str:
    return render_template("problem.md.j2", build_problem_context(problem))


def note_filename(problem: Problem) -> str:
    # Zero-padded so the files sort numerically in the vault: 0001-two-sum.md
    return f"{problem.leetcode_id:04d}-{problem.slug}.md"


def skip_reason(problem: Problem) -> str | None:
    """Why a note can't be generated yet, or None if the problem is ready."""
    if not problem.solved:
        return "not solved"
    if problem.leetcode_id is None:
        return "no LeetCode id yet"

    done = set(
        problem.sync_tasks.filter(status=SyncTask.Status.DONE).values_list(
            "part", flat=True
        )
    )
    for part in (
        SyncTask.Part.CONTENT,
        SyncTask.Part.IMAGES,
        SyncTask.Part.SUBMISSIONS,
    ):
        if part not in done:
            return f"{part} not synced"

    if not problem.submissions.exists():
        return "no submission stored"
    return None


def _copy_images(problem: Problem, output_dir: Path) -> int:
    """Copy this problem's images into <output_dir>/assets/<slug>/."""
    copied = 0
    for image in problem.images.exclude(file=""):
        src = Path(image.file.path)
        # Stored names already look like assets/<slug>/0.png, so they mirror 1:1.
        dest = output_dir / image.file.name

        if not src.exists():
            logger.warning("note_image_missing", slug=problem.slug, path=str(src))
            continue
        if dest.exists() and dest.stat().st_size == src.stat().st_size:
            continue  # already there

        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        copied += 1
    return copied


def write_problem_note(
    problem: Problem, output_dirs: Sequence[Path | str] | None = None
) -> NoteResult:
    """Render a problem note once and write it, with its images, to every output dir."""
    result = NoteResult(problem.slug)

    if reason := skip_reason(problem):
        result.skipped_reason = reason
        return result

    dirs = resolve_output_dirs(output_dirs)

    text = render_problem_note(problem)  # render once: a template error writes nothing

    for out in dirs:
        try:
            # No parents=True on purpose: if a drive isn't mounted, this fails
            # instead of quietly creating a folder on the wrong disk.
            layout = ensure_layout(out)
            copied = _copy_images(problem, layout.problems_dir)
            path = layout.problems_dir / problem_note_filename(problem)
            write_atomic(path, text)
        except OSError as exc:
            logger.warning(
                "note_write_failed", slug=problem.slug, dir=str(out), error=str(exc)
            )
            result.errors[str(out)] = str(exc)
            continue

        logger.info(
            "note_written", slug=problem.slug, path=str(path), images_copied=copied
        )
        result.paths.append(path)

    return result
