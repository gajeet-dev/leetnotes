import io
import json
import os
import shutil
import tarfile
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from problems.models import Problem, ProblemImage, SyncTask

logger = structlog.get_logger(__name__)

DEFAULT_BUNDLE_FILENAME = "problems_bundle.tar.gz"

PROBLEM_FIELDS = [
    "slug",
    "leetcode_id",
    "title",
    "url",
    "difficulty",
    "category",
    "tags",
    "paid_only",
    "raw_question_html",
    "content_remote_markdown",
    "content_local_html",
    "content_local_markdown",
    "content_text",
    "flowchart",
    "has_images",
]


@dataclass
class BundleExportResult:
    output_path: Path
    problems_count: int = 0
    images_count: int = 0
    tasks_count: int = 0
    media_files_count: int = 0
    missing_media_count: int = 0
    bundle_size_bytes: int = 0


@dataclass
class BundleImportResult:
    bundle_path: Path
    problems_created: int = 0
    problems_updated: int = 0
    images_created: int = 0
    images_existing: int = 0
    tasks_created: int = 0
    tasks_updated: int = 0
    tasks_existing: int = 0
    media_extracted: int = 0
    media_skipped: int = 0
    dry_run: bool = False


def export_bundle(
    output_path: Path | str | None = None,
    *,
    include_submissions: bool = False,
    preserve_solved: bool = False,
    stdout=None,
) -> BundleExportResult:
    """
    Extract problems, problem images, and content/image sync tasks (excluding submissions),
    along with media files, into a single distributable bundle archive (.tar.gz).
    Does NOT alter any data in the database.
    """
    start_time = time.monotonic()
    if output_path is None:
        target_path = Path(settings.BASE_DIR) / DEFAULT_BUNDLE_FILENAME
    else:
        target_path = Path(output_path).resolve()

    target_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Query Problems (read-only)
    problems_qs = Problem.objects.all().order_by("leetcode_id", "slug")
    problems_data: list[dict[str, Any]] = []
    for p in problems_qs.iterator(chunk_size=1000):
        item: dict[str, Any] = {f: getattr(p, f) for f in PROBLEM_FIELDS}
        item["solved"] = p.solved if preserve_solved else False
        problems_data.append(item)

    # 2. Query ProblemImages (read-only)
    images_qs = ProblemImage.objects.select_related("problem").order_by("id")
    images_data: list[dict[str, Any]] = []
    media_rel_paths: set[str] = set()
    for img in images_qs.iterator(chunk_size=1000):
        file_name = img.file.name if img.file else ""
        images_data.append(
            {
                "problem_slug": img.problem.slug,
                "source_url": img.source_url,
                "file": file_name,
            }
        )
        if file_name:
            media_rel_paths.add(file_name)

    # 3. Query SyncTasks: only 'content' and 'images' (exclude 'submissions' unless requested)
    tasks_qs = SyncTask.objects.select_related("problem")
    if not include_submissions:
        tasks_qs = tasks_qs.filter(
            part__in=[SyncTask.Part.CONTENT, SyncTask.Part.IMAGES]
        )
    tasks_qs = tasks_qs.order_by("problem__leetcode_id", "part")

    tasks_data: list[dict[str, Any]] = []
    for task in tasks_qs.iterator(chunk_size=2000):
        tasks_data.append(
            {
                "problem_slug": task.problem.slug,
                "part": task.part,
                "status": task.status,
                "attempts": task.attempts,
                "last_error": task.last_error,
                "completed_at": (
                    task.completed_at.isoformat() if task.completed_at else None
                ),
            }
        )

    # 4. Check media files on disk
    media_root = Path(settings.MEDIA_ROOT)
    valid_media_files: list[tuple[Path, str]] = []
    missing_media_count = 0
    for rel_path in sorted(media_rel_paths):
        abs_path = media_root / rel_path
        if abs_path.is_file():
            valid_media_files.append((abs_path, rel_path))
        else:
            missing_media_count += 1
            logger.warning("bundle_media_missing_on_disk", path=rel_path)

    manifest = {
        "version": 1,
        "format": "leetnotes-problems-bundle",
        "created_at": timezone.now().isoformat(),
        "preserve_solved": preserve_solved,
        "include_submissions": include_submissions,
        "counts": {
            "problems": len(problems_data),
            "images": len(images_data),
            "sync_tasks": len(tasks_data),
            "media_files": len(valid_media_files),
        },
    }

    bundle_payload = {
        "manifest": manifest,
        "problems": problems_data,
        "images": images_data,
        "tasks": tasks_data,
    }

    if stdout:
        stdout.write(
            f"Writing bundle to {target_path} ({len(problems_data)} problems, "
            f"{len(images_data)} images, {len(tasks_data)} tasks, "
            f"{len(valid_media_files)} media files)..."
        )

    now_epoch = int(time.time())
    with tarfile.open(target_path, "w:gz") as tar:
        # Add manifest.json
        manifest_bytes = json.dumps(manifest, indent=2).encode("utf-8")
        ti_manifest = tarfile.TarInfo(name="manifest.json")
        ti_manifest.size = len(manifest_bytes)
        ti_manifest.mtime = now_epoch
        tar.addfile(ti_manifest, io.BytesIO(manifest_bytes))

        # Add data.json
        data_bytes = json.dumps(bundle_payload, separators=(",", ":")).encode("utf-8")
        ti_data = tarfile.TarInfo(name="data.json")
        ti_data.size = len(data_bytes)
        ti_data.mtime = now_epoch
        tar.addfile(ti_data, io.BytesIO(data_bytes))

        # Add media files under media/ prefix
        for abs_path, rel_path in valid_media_files:
            arc_name = f"media/{rel_path}"
            tar.add(str(abs_path), arcname=arc_name)

    size_bytes = target_path.stat().st_size
    duration = time.monotonic() - start_time
    logger.info(
        "bundle_exported",
        path=str(target_path),
        size_bytes=size_bytes,
        problems=len(problems_data),
        images=len(images_data),
        tasks=len(tasks_data),
        media_files=len(valid_media_files),
        duration=duration,
    )

    return BundleExportResult(
        output_path=target_path,
        problems_count=len(problems_data),
        images_count=len(images_data),
        tasks_count=len(tasks_data),
        media_files_count=len(valid_media_files),
        missing_media_count=missing_media_count,
        bundle_size_bytes=size_bytes,
    )


def _load_data_and_extract_media(
    bundle_path: Path,
    media_root: Path,
    *,
    dry_run: bool,
    no_media: bool,
) -> tuple[dict[str, Any], int, int]:
    """
    Extract or read bundle from .tar.gz / .zip / directory.
    Returns (bundle_data, media_extracted, media_skipped).
    """
    media_extracted = 0
    media_skipped = 0

    if bundle_path.is_dir():
        data_file = bundle_path / "data.json"
        if not data_file.exists():
            raise FileNotFoundError(f"data.json not found in {bundle_path}")
        with open(data_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not no_media and not dry_run:
            bundle_media = bundle_path / "media"
            if bundle_media.exists():
                for root, _, files in os.walk(bundle_media):
                    for file in files:
                        src = Path(root) / file
                        rel = src.relative_to(bundle_media)
                        dst = media_root / rel
                        if dst.is_file() and dst.stat().st_size == src.stat().st_size:
                            media_skipped += 1
                            continue
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src, dst)
                        media_extracted += 1

        return data, media_extracted, media_skipped

    # Check tar archive
    if tarfile.is_tarfile(bundle_path):
        with tarfile.open(bundle_path, "r:*") as tar:
            try:
                member = tar.getmember("data.json")
            except KeyError:
                raise ValueError(f"Invalid bundle: 'data.json' missing in {bundle_path}")

            data_f = tar.extractfile(member)
            if data_f is None:
                raise ValueError("Could not read 'data.json' from bundle")
            data = json.load(data_f)

            if not no_media and not dry_run:
                media_root.mkdir(parents=True, exist_ok=True)
                canonical_media_root = media_root.resolve()

                for m in tar.getmembers():
                    if not m.isfile():
                        continue
                    name = m.name.lstrip("./")
                    if name.startswith("media/"):
                        rel = name[len("media/") :]
                    elif name.startswith("assets/"):
                        rel = name
                    else:
                        continue

                    dst = (media_root / rel).resolve()
                    # Security check: directory traversal
                    if not str(dst).startswith(str(canonical_media_root)):
                        raise ValueError(f"Security error: path traversal in {m.name}")

                    if dst.is_file() and dst.stat().st_size == m.size:
                        media_skipped += 1
                        continue

                    dst.parent.mkdir(parents=True, exist_ok=True)
                    f_in = tar.extractfile(m)
                    if f_in:
                        with open(dst, "wb") as f_out:
                            shutil.copyfileobj(f_in, f_out)
                        media_extracted += 1

        return data, media_extracted, media_skipped

    # Check zip archive
    if zipfile.is_zipfile(bundle_path):
        with zipfile.ZipFile(bundle_path, "r") as zf:
            try:
                data_bytes = zf.read("data.json")
            except KeyError:
                raise ValueError(f"Invalid bundle: 'data.json' missing in {bundle_path}")
            data = json.loads(data_bytes)

            if not no_media and not dry_run:
                media_root.mkdir(parents=True, exist_ok=True)
                canonical_media_root = media_root.resolve()

                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    name = info.filename.lstrip("./")
                    if name.startswith("media/"):
                        rel = name[len("media/") :]
                    elif name.startswith("assets/"):
                        rel = name
                    else:
                        continue

                    dst = (media_root / rel).resolve()
                    if not str(dst).startswith(str(canonical_media_root)):
                        raise ValueError(f"Security error: path traversal in {info.filename}")

                    if dst.is_file() and dst.stat().st_size == info.file_size:
                        media_skipped += 1
                        continue

                    dst.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as f_in, open(dst, "wb") as f_out:
                        shutil.copyfileobj(f_in, f_out)
                    media_extracted += 1

        return data, media_extracted, media_skipped

    raise ValueError(f"Unsupported bundle format or file not found: {bundle_path}")


def import_bundle(
    bundle_path: Path | str | None = None,
    *,
    dry_run: bool = False,
    no_media: bool = False,
    no_tasks: bool = False,
    reset_solved: bool = True,
    batch_size: int = 500,
    stdout=None,
) -> BundleImportResult:
    """
    Import problems, problem images, and content/image sync tasks from a bundle.
    Extracts media files into settings.MEDIA_ROOT.
    Never alters or overwrites existing user submissions or solved status if already True.
    """
    if bundle_path is None:
        target_path = Path(settings.BASE_DIR) / DEFAULT_BUNDLE_FILENAME
    else:
        target_path = Path(bundle_path).resolve()

    if not target_path.exists():
        raise FileNotFoundError(f"Bundle file not found: {target_path}")

    media_root = Path(settings.MEDIA_ROOT)
    data, media_extracted, media_skipped = _load_data_and_extract_media(
        target_path,
        media_root,
        dry_run=dry_run,
        no_media=no_media,
    )

    problems_list = data.get("problems", [])
    images_list = data.get("images", [])
    tasks_list = data.get("tasks", [])

    problems_created = 0
    problems_updated = 0
    images_created = 0
    images_existing = 0
    tasks_created = 0
    tasks_updated = 0
    tasks_existing = 0

    with transaction.atomic():
        # 1. Import Problems
        existing_problems = {p.slug: p for p in Problem.objects.all()}
        to_create_problems: list[Problem] = []
        to_update_problems: list[Problem] = []

        update_fields = [
            "leetcode_id",
            "title",
            "url",
            "difficulty",
            "category",
            "tags",
            "paid_only",
            "raw_question_html",
            "content_remote_markdown",
            "content_local_html",
            "content_local_markdown",
            "content_text",
            "flowchart",
            "has_images",
        ]

        for p_data in problems_list:
            slug = p_data["slug"]
            target_solved = False if reset_solved else p_data.get("solved", False)

            if slug in existing_problems:
                existing = existing_problems[slug]
                changed = False
                for field in update_fields:
                    val = p_data.get(field)
                    if getattr(existing, field) != val:
                        setattr(existing, field, val)
                        changed = True

                # If existing is already True, preserve it. If false and target is True, promote.
                if not existing.solved and target_solved:
                    existing.solved = True
                    changed = True

                if changed:
                    to_update_problems.append(existing)
                    problems_updated += 1
            else:
                p_obj = Problem(
                    slug=slug,
                    leetcode_id=p_data.get("leetcode_id"),
                    title=p_data.get("title"),
                    url=p_data.get("url"),
                    difficulty=p_data.get("difficulty"),
                    category=p_data.get("category"),
                    tags=p_data.get("tags"),
                    solved=target_solved,
                    paid_only=p_data.get("paid_only", False),
                    raw_question_html=p_data.get("raw_question_html"),
                    content_remote_markdown=p_data.get("content_remote_markdown"),
                    content_local_html=p_data.get("content_local_html"),
                    content_local_markdown=p_data.get("content_local_markdown"),
                    content_text=p_data.get("content_text"),
                    flowchart=p_data.get("flowchart"),
                    has_images=p_data.get("has_images"),
                )
                to_create_problems.append(p_obj)
                problems_created += 1

        if not dry_run:
            if to_create_problems:
                Problem.objects.bulk_create(
                    to_create_problems, batch_size=batch_size, ignore_conflicts=True
                )
            if to_update_problems:
                Problem.objects.bulk_update(
                    to_update_problems, fields=update_fields, batch_size=batch_size
                )

        slug_to_id = dict(Problem.objects.values_list("slug", "id"))

        # 2. Import ProblemImages
        existing_images = set(
            ProblemImage.objects.values_list("problem__slug", "source_url")
        )
        to_create_images: list[ProblemImage] = []

        for img_data in images_list:
            slug = img_data["problem_slug"]
            source_url = img_data["source_url"]
            if (slug, source_url) in existing_images:
                images_existing += 1
                continue

            prob_id = slug_to_id.get(slug)
            if not prob_id:
                continue

            to_create_images.append(
                ProblemImage(
                    problem_id=prob_id,
                    source_url=source_url,
                    file=img_data.get("file", ""),
                )
            )
            images_created += 1

        if not dry_run and to_create_images:
            ProblemImage.objects.bulk_create(
                to_create_images, batch_size=batch_size, ignore_conflicts=True
            )

        # 3. Import SyncTasks (only content and images)
        if not no_tasks:
            existing_tasks = {
                (t.problem_id, t.part): t
                for t in SyncTask.objects.filter(
                    part__in=[SyncTask.Part.CONTENT, SyncTask.Part.IMAGES]
                )
            }
            to_create_tasks: list[SyncTask] = []
            to_update_tasks: list[SyncTask] = []

            for task_data in tasks_list:
                part = task_data.get("part")
                # Never import submissions tasks from the bundle!
                if part not in (SyncTask.Part.CONTENT, SyncTask.Part.IMAGES):
                    continue

                slug = task_data["problem_slug"]
                prob_id = slug_to_id.get(slug)
                if not prob_id:
                    continue

                key = (prob_id, part)
                status = task_data.get("status", SyncTask.Status.PENDING)
                attempts = task_data.get("attempts", 0)
                last_error = task_data.get("last_error", "")
                completed_str = task_data.get("completed_at")
                completed_at = (
                    datetime.fromisoformat(completed_str) if completed_str else None
                )

                if key in existing_tasks:
                    existing_t = existing_tasks[key]
                    # If existing task is pending/failed and bundle is done, upgrade to done
                    if (
                        existing_t.status != SyncTask.Status.DONE
                        and status == SyncTask.Status.DONE
                    ):
                        existing_t.status = status
                        existing_t.attempts = attempts
                        existing_t.last_error = last_error
                        existing_t.completed_at = completed_at
                        to_update_tasks.append(existing_t)
                        tasks_updated += 1
                    else:
                        tasks_existing += 1
                else:
                    to_create_tasks.append(
                        SyncTask(
                            problem_id=prob_id,
                            part=part,
                            status=status,
                            attempts=attempts,
                            last_error=last_error,
                            completed_at=completed_at,
                        )
                    )
                    tasks_created += 1

            if not dry_run:
                if to_create_tasks:
                    SyncTask.objects.bulk_create(
                        to_create_tasks, batch_size=batch_size, ignore_conflicts=True
                    )
                if to_update_tasks:
                    SyncTask.objects.bulk_update(
                        to_update_tasks,
                        fields=["status", "attempts", "last_error", "completed_at"],
                        batch_size=batch_size,
                    )

        if dry_run:
            transaction.set_rollback(True)

    result = BundleImportResult(
        bundle_path=target_path,
        problems_created=problems_created,
        problems_updated=problems_updated,
        images_created=images_created,
        images_existing=images_existing,
        tasks_created=tasks_created,
        tasks_updated=tasks_updated,
        tasks_existing=tasks_existing,
        media_extracted=media_extracted,
        media_skipped=media_skipped,
        dry_run=dry_run,
    )

    logger.info("bundle_imported", **result.__dict__)
    return result
