import json
import tarfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog
from django.conf import settings
from django.core.management import call_command
from django.db import connection, transaction

from notes.models import NoteRecord
from problems.models import Submission, SyncTask
from userconfig.paths import db_snapshots_dir

logger = structlog.get_logger(__name__)


def format_file_size(size_bytes: int) -> str:
    """Return a human-readable string representation of a file size in bytes."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        kilobytes = size_bytes / 1024
        return f"{kilobytes:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        megabytes = size_bytes / (1024 * 1024)
        return f"{megabytes:.1f} MB"
    else:
        gigabytes = size_bytes / (1024 * 1024 * 1024)
        return f"{gigabytes:.1f} GB"


def read_manifest_from_archive(archive_path: Path) -> dict[str, Any] | None:
    """Read manifest.json from a tar.gz or zip archive without extracting the media files."""
    if tarfile.is_tarfile(archive_path):
        try:
            with tarfile.open(archive_path, "r:*") as tar_archive:
                try:
                    manifest_member = tar_archive.getmember("manifest.json")
                    manifest_file = tar_archive.extractfile(manifest_member)
                    if manifest_file is not None:
                        manifest_content = manifest_file.read()
                        return json.loads(manifest_content.decode("utf-8"))
                except KeyError, json.JSONDecodeError:
                    return None
        except Exception:
            return None

    if zipfile.is_zipfile(archive_path):
        try:
            with zipfile.ZipFile(archive_path, "r") as zip_archive:
                try:
                    manifest_bytes = zip_archive.read("manifest.json")
                    return json.loads(manifest_bytes.decode("utf-8"))
                except KeyError, json.JSONDecodeError:
                    return None
        except Exception:
            return None

    return None


def list_snapshots() -> list[dict[str, Any]]:
    """Scan the db_snapshots directory and return metadata for each snapshot file."""
    snapshots_directory = db_snapshots_dir()
    snapshots_list: list[dict[str, Any]] = []

    if not snapshots_directory.exists():
        return snapshots_list

    for entry in snapshots_directory.iterdir():
        if not entry.is_file():
            continue

        filename_lower = entry.name.lower()
        is_supported_archive = (
            filename_lower.endswith(".tar.gz")
            or filename_lower.endswith(".tgz")
            or filename_lower.endswith(".zip")
        )
        if not is_supported_archive:
            continue

        file_stat = entry.stat()
        file_size_bytes = file_stat.st_size
        modified_epoch = file_stat.st_mtime
        modified_datetime = datetime.fromtimestamp(modified_epoch)

        manifest = read_manifest_from_archive(entry)
        problems_count = 0
        images_count = 0
        submissions_count = 0
        ai_responses_count = 0
        note_records_count = 0
        has_submissions = False
        created_timestamp_str = modified_datetime.strftime("%Y-%m-%d %H:%M")

        if manifest and isinstance(manifest, dict):
            counts = manifest.get("counts", {})
            problems_count = counts.get("problems", 0)
            images_count = counts.get("images", 0)
            submissions_count = counts.get("submissions", 0)
            ai_responses_count = counts.get("ai_responses", 0)
            note_records_count = counts.get("note_records", 0)
            has_submissions = bool(manifest.get("include_submissions", False))
            raw_created = manifest.get("created_at")
            if raw_created:
                try:
                    dt = datetime.fromisoformat(raw_created)
                    created_timestamp_str = dt.strftime("%Y-%m-%d %H:%M")
                except ValueError:
                    pass

        snapshot_info = {
            "filename": entry.name,
            "path": str(entry),
            "size_bytes": file_size_bytes,
            "size_formatted": format_file_size(file_size_bytes),
            "modified_epoch": modified_epoch,
            "created_display": created_timestamp_str,
            "problems_count": problems_count,
            "images_count": images_count,
            "submissions_count": submissions_count,
            "ai_responses_count": ai_responses_count,
            "note_records_count": note_records_count,
            "has_submissions": has_submissions,
        }
        snapshots_list.append(snapshot_info)

    # Sort snapshots by modification time descending (newest snapshots first)
    snapshots_list.sort(key=lambda s: s["modified_epoch"], reverse=True)
    return snapshots_list


def reset_user_submissions() -> dict[str, int]:
    """Delete all user submissions and submissions sync tasks, leaving problem solved status untouched."""
    with transaction.atomic():
        # Fetch and delete all user submission records from the database
        deleted_submissions_count, _ = Submission.objects.all().delete()

        # Fetch and delete all sync tasks associated with the submissions part
        deleted_tasks_tuple = SyncTask.objects.filter(
            part=SyncTask.Part.SUBMISSIONS
        ).delete()
        deleted_tasks_count = deleted_tasks_tuple[0]

        logger.info(
            "user_submissions_reset",
            submissions_deleted=deleted_submissions_count,
            tasks_deleted=deleted_tasks_count,
        )

        result = {
            "submissions_deleted": deleted_submissions_count,
            "tasks_deleted": deleted_tasks_count,
        }
        return result


def clear_note_records() -> dict[str, int]:
    """Delete all NoteRecord tracking rows from the database without touching markdown files on disk."""
    with transaction.atomic():
        # Fetch and delete all note synchronization records from the database
        deleted_records_count, _ = NoteRecord.objects.all().delete()

        logger.info("note_records_cleared", records_deleted=deleted_records_count)

        result = {
            "note_records_deleted": deleted_records_count,
        }
        return result


def reinit_database() -> dict[str, Any]:
    """Wipe the SQLite database file completely and re-run all Django migrations for a clean start."""
    database_setting_path = settings.DATABASES["default"]["NAME"]
    database_file = Path(database_setting_path).resolve()

    # Close the existing database connection cleanly so the file can be removed
    connection.close()

    removed_files: list[str] = []

    # Remove main database file if it exists on disk
    if database_file.exists():
        database_file.unlink()
        removed_files.append(str(database_file))

    # Remove companion WAL and SHM journal files if SQLite is running in WAL mode
    wal_file = database_file.with_name(database_file.name + "-wal")
    if wal_file.exists():
        wal_file.unlink()
        removed_files.append(str(wal_file))

    shm_file = database_file.with_name(database_file.name + "-shm")
    if shm_file.exists():
        shm_file.unlink()
        removed_files.append(str(shm_file))

    # Re-run all migrations from scratch to recreate fresh database tables
    call_command("migrate", interactive=False)

    logger.info(
        "database_reinitialized",
        database_path=str(database_file),
        removed_files=removed_files,
    )

    result = {
        "success": True,
        "database_file": str(database_file),
        "removed_files": removed_files,
    }
    return result
