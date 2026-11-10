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

from problems.models import Problem, ProblemImage, Submission, SyncTask
from prefill.models import AIResponse
from notes.models import NoteRecord

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
    submissions_count: int = 0
    ai_responses_count: int = 0
    note_records_count: int = 0
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
    submissions_created: int = 0
    submissions_updated: int = 0
    ai_responses_created: int = 0
    ai_responses_updated: int = 0
    note_records_created: int = 0
    note_records_updated: int = 0
    media_extracted: int = 0
    media_skipped: int = 0
    dry_run: bool = False


def export_bundle(
    output_path: Path | str | None = None,
    *,
    include_submissions: bool = False,
    include_ai_notes: bool = True,
    preserve_solved: bool = False,
    include_note_records: bool = False,
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

    # Fetch all problem records ordered by their LeetCode ID and problem slug
    problems_qs = Problem.objects.all().order_by("leetcode_id", "slug")
    problems_data: list[dict[str, Any]] = []
    for p in problems_qs.iterator(chunk_size=1000):
        item: dict[str, Any] = {}
        for field_name in PROBLEM_FIELDS:
            item[field_name] = getattr(p, field_name)
        item["solved"] = p.solved if preserve_solved else False
        problems_data.append(item)

    # Fetch all problem images joined with their parent problem
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

    # Fetch content and image sync tasks joined with problem, optionally including submission tasks
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

    # Fetch user submissions joined with problem if the user requested submissions in the export
    submissions_data: list[dict[str, Any]] = []
    if include_submissions:
        submissions_qs = Submission.objects.select_related("problem").order_by(
            "problem__leetcode_id"
        )
        for sub in submissions_qs.iterator(chunk_size=1000):
            submission_date_str = None
            if sub.submission_date:
                submission_date_str = sub.submission_date.isoformat()
            submissions_data.append(
                {
                    "problem_slug": sub.problem.slug,
                    "lang": sub.lang,
                    "code": sub.code,
                    "submission_date": submission_date_str,
                    "pinned": sub.pinned,
                }
            )

    # Fetch all AI generated editorial solution notes joined with problem if requested
    ai_responses_data: list[dict[str, Any]] = []
    if include_ai_notes:
        ai_responses_qs = AIResponse.objects.select_related("problem").order_by(
            "problem__leetcode_id", "created_at"
        )
        for ai_resp in ai_responses_qs.iterator(chunk_size=1000):
            created_at_str = None
            if ai_resp.created_at:
                created_at_str = ai_resp.created_at.isoformat()
            ai_responses_data.append(
                {
                    "problem_slug": ai_resp.problem.slug,
                    "source": ai_resp.source,
                    "provider_name": ai_resp.provider_name,
                    "model_name": ai_resp.model_name,
                    "prompt_version": ai_resp.prompt_version,
                    "input_hash": ai_resp.input_hash,
                    "raw_text": ai_resp.raw_text,
                    "parsed": ai_resp.parsed,
                    "status": ai_resp.status,
                    "error": ai_resp.error,
                    "is_active": ai_resp.is_active,
                    "created_at": created_at_str,
                }
            )

    # Fetch all note synchronization records joined with problem if requested
    note_records_data: list[dict[str, Any]] = []
    if include_note_records:
        note_records_qs = NoteRecord.objects.select_related("problem").order_by(
            "problem__leetcode_id", "kind"
        )
        for rec in note_records_qs.iterator(chunk_size=1000):
            written_at_str = None
            if rec.written_at:
                written_at_str = rec.written_at.isoformat()
            note_records_data.append(
                {
                    "problem_slug": rec.problem.slug,
                    "kind": rec.kind,
                    "output_dir": rec.output_dir,
                    "relative_path": rec.relative_path,
                    "content_hash": rec.content_hash,
                    "written_at": written_at_str,
                }
            )

    # Check media files on disk
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
        "version": 2,
        "format": "leetnotes-problems-bundle",
        "created_at": timezone.now().isoformat(),
        "preserve_solved": preserve_solved,
        "include_submissions": include_submissions,
        "include_ai_notes": include_ai_notes,
        "include_note_records": include_note_records,
        "counts": {
            "problems": len(problems_data),
            "images": len(images_data),
            "sync_tasks": len(tasks_data),
            "submissions": len(submissions_data),
            "ai_responses": len(ai_responses_data),
            "note_records": len(note_records_data),
            "media_files": len(valid_media_files),
        },
    }

    bundle_payload = {
        "manifest": manifest,
        "problems": problems_data,
        "images": images_data,
        "tasks": tasks_data,
        "submissions": submissions_data,
        "ai_responses": ai_responses_data,
        "note_records": note_records_data,
    }

    if stdout:
        stdout.write(
            f"Writing bundle to {target_path} ({len(problems_data)} problems, "
            f"{len(images_data)} images, {len(tasks_data)} tasks, "
            f"{len(submissions_data)} submissions, {len(ai_responses_data)} AI notes, "
            f"{len(note_records_data)} note records, {len(valid_media_files)} media files)..."
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
        submissions=len(submissions_data),
        ai_responses=len(ai_responses_data),
        note_records=len(note_records_data),
        media_files=len(valid_media_files),
        duration=duration,
    )

    return BundleExportResult(
        output_path=target_path,
        problems_count=len(problems_data),
        images_count=len(images_data),
        tasks_count=len(tasks_data),
        submissions_count=len(submissions_data),
        ai_responses_count=len(ai_responses_data),
        note_records_count=len(note_records_data),
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
                raise ValueError(
                    f"Invalid bundle: 'data.json' missing in {bundle_path}"
                )

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
                raise ValueError(
                    f"Invalid bundle: 'data.json' missing in {bundle_path}"
                )
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
                        raise ValueError(
                            f"Security error: path traversal in {info.filename}"
                        )

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
    submissions_list = data.get("submissions", [])
    ai_responses_list = data.get("ai_responses", [])
    note_records_list = data.get("note_records", [])

    problems_created = 0
    problems_updated = 0
    images_created = 0
    images_existing = 0
    tasks_created = 0
    tasks_updated = 0
    tasks_existing = 0
    submissions_created = 0
    submissions_updated = 0
    ai_responses_created = 0
    ai_responses_updated = 0
    note_records_created = 0
    note_records_updated = 0

    with transaction.atomic():
        # 1. Import Problems
        existing_problems = {}
        # Fetch all existing problem records from the database to check for updates
        for existing_problem in Problem.objects.all():
            existing_problems[existing_problem.slug] = existing_problem

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

        slug_to_id = {}
        # Fetch mapping from problem slug to problem primary key ID
        for problem_slug, problem_id in Problem.objects.values_list("slug", "id"):
            slug_to_id[problem_slug] = problem_id

        # 2. Import ProblemImages
        existing_images = set()
        # Fetch existing problem image pairs of problem slug and source url
        for img_tuple in ProblemImage.objects.values_list(
            "problem__slug", "source_url"
        ):
            existing_images.add(img_tuple)

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

        # 3. Import SyncTasks
        if not no_tasks:
            existing_tasks = {}
            # Fetch existing sync tasks for each problem and sync part
            for existing_task in SyncTask.objects.all():
                task_key = (existing_task.problem_id, existing_task.part)
                existing_tasks[task_key] = existing_task

            to_create_tasks: list[SyncTask] = []
            to_update_tasks: list[SyncTask] = []

            for task_data in tasks_list:
                part = task_data.get("part")
                # Exclude submission tasks unless submissions are included in this bundle
                if part not in (SyncTask.Part.CONTENT, SyncTask.Part.IMAGES):
                    if part == SyncTask.Part.SUBMISSIONS and not submissions_list:
                        continue
                    elif part != SyncTask.Part.SUBMISSIONS:
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

        # 4. Import Submissions (if present in the bundle)
        if submissions_list:
            existing_submissions = {}
            # Fetch all existing accepted submissions mapped by problem primary key ID
            for existing_submission in Submission.objects.all():
                existing_submissions[existing_submission.problem_id] = (
                    existing_submission
                )

            to_create_submissions: list[Submission] = []
            to_update_submissions: list[Submission] = []
            problems_with_submissions_ids: list[int] = []

            for sub_data in submissions_list:
                problem_slug = sub_data.get("problem_slug")
                prob_id = slug_to_id.get(problem_slug)
                if not prob_id:
                    continue

                date_string = sub_data.get("submission_date")
                sub_date = None
                if date_string:
                    try:
                        sub_date = datetime.fromisoformat(date_string)
                    except ValueError:
                        sub_date = timezone.now()
                else:
                    sub_date = timezone.now()

                language = sub_data.get("lang", "")
                code_text = sub_data.get("code", "")
                is_pinned = sub_data.get("pinned", False)

                if prob_id in existing_submissions:
                    existing_sub = existing_submissions[prob_id]
                    existing_sub.lang = language
                    existing_sub.code = code_text
                    existing_sub.submission_date = sub_date
                    existing_sub.pinned = is_pinned
                    to_update_submissions.append(existing_sub)
                    submissions_updated += 1
                else:
                    new_submission = Submission(
                        problem_id=prob_id,
                        lang=language,
                        code=code_text,
                        submission_date=sub_date,
                        pinned=is_pinned,
                    )
                    to_create_submissions.append(new_submission)
                    submissions_created += 1

                problems_with_submissions_ids.append(prob_id)

            if not dry_run:
                if to_create_submissions:
                    Submission.objects.bulk_create(
                        to_create_submissions,
                        batch_size=batch_size,
                        ignore_conflicts=True,
                    )
                if to_update_submissions:
                    Submission.objects.bulk_update(
                        to_update_submissions,
                        fields=["lang", "code", "submission_date", "pinned"],
                        batch_size=batch_size,
                    )
                if problems_with_submissions_ids:
                    # Update problems that have imported submissions to solved=True
                    Problem.objects.filter(
                        id__in=problems_with_submissions_ids, solved=False
                    ).update(solved=True)

        # 5. Import AI Editorial Notes (if present in the bundle)
        if ai_responses_list:
            existing_active_ai_responses = {}
            # Fetch existing active AI responses to maintain one active response per problem
            for active_resp in AIResponse.objects.filter(is_active=True):
                existing_active_ai_responses[active_resp.problem_id] = active_resp

            to_create_ai_responses: list[AIResponse] = []
            to_update_ai_responses: list[AIResponse] = []

            for ai_data in ai_responses_list:
                problem_slug = ai_data.get("problem_slug")
                prob_id = slug_to_id.get(problem_slug)
                if not prob_id:
                    continue

                source = ai_data.get("source", AIResponse.Source.PROVIDER)
                provider_name = ai_data.get("provider_name", "")
                model_name = ai_data.get("model_name", "")
                prompt_version = ai_data.get("prompt_version", "")
                input_hash = ai_data.get("input_hash", "")
                raw_text = ai_data.get("raw_text", "")
                parsed = ai_data.get("parsed")
                status = ai_data.get("status", AIResponse.Status.VALID)
                error = ai_data.get("error", "")
                is_active = ai_data.get("is_active", True)

                if is_active and prob_id in existing_active_ai_responses:
                    existing_resp = existing_active_ai_responses[prob_id]
                    existing_resp.source = source
                    existing_resp.provider_name = provider_name
                    existing_resp.model_name = model_name
                    existing_resp.prompt_version = prompt_version
                    existing_resp.input_hash = input_hash
                    existing_resp.raw_text = raw_text
                    existing_resp.parsed = parsed
                    existing_resp.status = status
                    existing_resp.error = error
                    to_update_ai_responses.append(existing_resp)
                    ai_responses_updated += 1
                else:
                    new_ai_response = AIResponse(
                        problem_id=prob_id,
                        source=source,
                        provider_name=provider_name,
                        model_name=model_name,
                        prompt_version=prompt_version,
                        input_hash=input_hash,
                        raw_text=raw_text,
                        parsed=parsed,
                        status=status,
                        error=error,
                        is_active=is_active,
                    )
                    to_create_ai_responses.append(new_ai_response)
                    ai_responses_created += 1

            if not dry_run:
                if to_create_ai_responses:
                    AIResponse.objects.bulk_create(
                        to_create_ai_responses,
                        batch_size=batch_size,
                        ignore_conflicts=True,
                    )
                if to_update_ai_responses:
                    AIResponse.objects.bulk_update(
                        to_update_ai_responses,
                        fields=[
                            "source",
                            "provider_name",
                            "model_name",
                            "prompt_version",
                            "input_hash",
                            "raw_text",
                            "parsed",
                            "status",
                            "error",
                        ],
                        batch_size=batch_size,
                    )

        # 6. Import Note Records (if present in the bundle)
        if note_records_list:
            existing_note_records = {}
            # Fetch existing note tracking records from the database
            for existing_record in NoteRecord.objects.all():
                record_key = (
                    existing_record.problem_id,
                    existing_record.kind,
                    existing_record.output_dir,
                )
                existing_note_records[record_key] = existing_record

            to_create_note_records: list[NoteRecord] = []
            to_update_note_records: list[NoteRecord] = []

            for nr_data in note_records_list:
                problem_slug = nr_data.get("problem_slug")
                prob_id = slug_to_id.get(problem_slug)
                if not prob_id:
                    continue

                kind = nr_data.get("kind", NoteRecord.Kind.PROBLEM)
                output_dir = nr_data.get("output_dir", "")
                relative_path = nr_data.get("relative_path", "")
                content_hash = nr_data.get("content_hash", "")
                written_at_str = nr_data.get("written_at")
                written_at_dt = None
                if written_at_str:
                    try:
                        written_at_dt = datetime.fromisoformat(written_at_str)
                    except ValueError:
                        pass
                if not written_at_dt:
                    written_at_dt = timezone.now()

                lookup_key = (prob_id, kind, output_dir)
                if lookup_key in existing_note_records:
                    existing_nr = existing_note_records[lookup_key]
                    existing_nr.relative_path = relative_path
                    existing_nr.content_hash = content_hash
                    existing_nr.written_at = written_at_dt
                    to_update_note_records.append(existing_nr)
                    note_records_updated += 1
                else:
                    new_nr = NoteRecord(
                        problem_id=prob_id,
                        kind=kind,
                        output_dir=output_dir,
                        relative_path=relative_path,
                        content_hash=content_hash,
                        written_at=written_at_dt,
                    )
                    to_create_note_records.append(new_nr)
                    note_records_created += 1

            if not dry_run:
                if to_create_note_records:
                    NoteRecord.objects.bulk_create(
                        to_create_note_records,
                        batch_size=batch_size,
                        ignore_conflicts=True,
                    )
                if to_update_note_records:
                    NoteRecord.objects.bulk_update(
                        to_update_note_records,
                        fields=["relative_path", "content_hash", "written_at"],
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
        submissions_created=submissions_created,
        submissions_updated=submissions_updated,
        ai_responses_created=ai_responses_created,
        ai_responses_updated=ai_responses_updated,
        note_records_created=note_records_created,
        note_records_updated=note_records_updated,
        media_extracted=media_extracted,
        media_skipped=media_skipped,
        dry_run=dry_run,
    )

    logger.info("bundle_imported", **result.__dict__)
    return result
