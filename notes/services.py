import shutil
from functools import partial
from pathlib import Path

import structlog

from problems.models import Problem, SyncTask

from .context import build_problem_context
from .layout import ensure_layout
from .models import NoteRecord
from .rendering import render_template
from .writer import NoteWriteResult, write_to_folders

logger = structlog.get_logger(__name__)


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


def prepare_images(problem: Problem, output_dir: Path) -> str | None:
    """Copy the problem's images into the folder. Returns an error message, or None."""
    try:
        layout = ensure_layout(output_dir)
        _copy_images(problem, layout.problems_dir)
    except OSError as exc:
        logger.warning(
            "note_images_failed", slug=problem.slug, dir=str(output_dir), error=str(exc)
        )
        return str(exc)
    return None


def write_problem_note(
    problem: Problem,
    output_dirs=None,
    *,
    replace_edited: bool = False,
) -> NoteWriteResult:
    """Render the problem note once and write it, with its images, into every output folder."""
    if reason := skip_reason(problem):
        return NoteWriteResult(
            problem.slug, NoteRecord.Kind.PROBLEM, skipped_reason=reason
        )

    text = render_problem_note(problem)  # render first: a failure writes nothing
    return write_to_folders(
        problem,
        NoteRecord.Kind.PROBLEM,
        text,
        output_dirs,
        replace_edited=replace_edited,
        before_write=partial(prepare_images, problem),
    )
