import tempfile
from datetime import datetime
from pathlib import Path

import structlog
from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from problems.services.bundle import export_bundle, import_bundle
from problems.services.snapshots import (
    clear_note_records,
    list_snapshots,
    reinit_database,
    reset_user_submissions,
)
from userconfig.paths import db_snapshots_dir

logger = structlog.get_logger(__name__)


@require_POST
def export_snapshot_view(request):
    """Export problems, images, AI editorial notes, and optionally submissions or note records to a bundle archive."""
    destination = request.POST.get("destination", "folder")
    custom_filename = (request.POST.get("filename") or "").strip()
    export_all = request.POST.get("export_all") in ("true", "1", "on")

    include_submissions = (
        request.POST.get("include_submissions") in ("true", "1", "on")
    ) or export_all
    include_ai_notes = (
        request.POST.get("include_ai_notes") in ("true", "1", "on")
    ) or export_all
    include_note_records = (
        request.POST.get("include_note_records") in ("true", "1", "on")
    ) or export_all
    # Solved status is automatically preserved when user submissions are included
    preserve_solved = include_submissions

    if not custom_filename:
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        if export_all:
            custom_filename = f"db_complete_{timestamp_str}.tar.gz"
        elif include_submissions:
            custom_filename = f"snapshot_full_{timestamp_str}.tar.gz"
        else:
            custom_filename = f"snapshot_problems_{timestamp_str}.tar.gz"
    elif not (custom_filename.endswith(".tar.gz") or custom_filename.endswith(".tgz")):
        custom_filename = f"{custom_filename}.tar.gz"

    snapshots_folder = db_snapshots_dir()
    target_path = snapshots_folder / custom_filename

    try:
        export_result = export_bundle(
            output_path=target_path,
            include_submissions=include_submissions,
            include_ai_notes=include_ai_notes,
            preserve_solved=preserve_solved,
            include_note_records=include_note_records,
        )
    except Exception as exc:
        logger.error("export_snapshot_failed", error=str(exc))
        if request.headers.get("HX-Request"):
            return render(
                request,
                "ui/partials/toast.html",
                {
                    "status": "destructive",
                    "message": f"Export failed: {exc}",
                    "persistent": True,
                },
            )
        return redirect("ui:settings")

    if destination == "download":
        return FileResponse(
            open(target_path, "rb"),
            as_attachment=True,
            filename=custom_filename,
            content_type="application/gzip",
        )

    toast_message = (
        f"Snapshot saved to db_snapshots/{custom_filename} "
        f"({export_result.problems_count} problems, {export_result.images_count} images, "
        f"{export_result.submissions_count} submissions, {export_result.ai_responses_count} AI notes, "
        f"{export_result.note_records_count} note records)."
    )
    if request.headers.get("HX-Request"):
        toast_html = render(
            request,
            "ui/partials/toast.html",
            {
                "status": "success",
                "message": toast_message,
                "persistent": True,
            },
        ).content.decode("utf-8")

        available_snapshots = list_snapshots()

        # Re-render snapshots dropdown as an out-of-band swap with newly created snapshot selected
        dropdown_html = render(
            request,
            "ui/partials/snapshot_dropdown.html",
            {
                "available_snapshots": available_snapshots,
                "selected_snapshot": custom_filename,
                "oob": True,
            },
        ).content.decode("utf-8")

        # Re-render snapshots list as an out-of-band swap
        snapshots_html = render(
            request,
            "ui/partials/snapshots_list.html",
            {
                "available_snapshots": available_snapshots,
                "oob": True,
            },
        ).content.decode("utf-8")

        return HttpResponse(toast_html + dropdown_html + snapshots_html)

    return redirect("ui:settings")


@require_POST
def import_snapshot_view(request):
    """Import problems, images, AI notes, submissions, and note records from a bundle archive."""
    source = request.POST.get("source", "snapshot_file")
    dry_run = request.POST.get("dry_run") in ("true", "1", "on")
    reset_solved = request.POST.get("reset_solved") in ("true", "1", "on")

    temp_file_to_clean = None

    if source == "upload":
        uploaded_file = request.FILES.get("bundle_file")
        if not uploaded_file:
            if request.headers.get("HX-Request"):
                return render(
                    request,
                    "ui/partials/toast.html",
                    {
                        "status": "destructive",
                        "message": "No bundle file was uploaded. Please choose a .tar.gz file.",
                        "persistent": True,
                    },
                )
            return redirect("ui:settings")

        temp_dir = tempfile.gettempdir()
        temp_bundle_path = Path(temp_dir) / uploaded_file.name
        with open(temp_bundle_path, "wb") as destination_file:
            for chunk in uploaded_file.chunks():
                destination_file.write(chunk)
        target_path = temp_bundle_path
        temp_file_to_clean = temp_bundle_path
    else:
        snapshot_filename = (request.POST.get("snapshot_filename") or "").strip()
        if not snapshot_filename:
            if request.headers.get("HX-Request"):
                return render(
                    request,
                    "ui/partials/toast.html",
                    {
                        "status": "destructive",
                        "message": "Please select a snapshot file to import.",
                        "persistent": True,
                    },
                )
            return redirect("ui:settings")

        safe_filename = Path(snapshot_filename).name
        target_path = db_snapshots_dir() / safe_filename
        if not target_path.is_file():
            if request.headers.get("HX-Request"):
                return render(
                    request,
                    "ui/partials/toast.html",
                    {
                        "status": "destructive",
                        "message": f"Snapshot file not found: {safe_filename}",
                        "persistent": True,
                    },
                )
            return redirect("ui:settings")

    try:
        import_result = import_bundle(
            bundle_path=target_path,
            dry_run=dry_run,
            reset_solved=reset_solved,
        )
    except Exception as exc:
        logger.error("import_snapshot_failed", path=str(target_path), error=str(exc))
        if temp_file_to_clean and temp_file_to_clean.exists():
            temp_file_to_clean.unlink(missing_ok=True)
        if request.headers.get("HX-Request"):
            return render(
                request,
                "ui/partials/toast.html",
                {
                    "status": "destructive",
                    "message": f"Import failed: {exc}",
                    "persistent": True,
                },
            )
        return redirect("ui:settings")

    if temp_file_to_clean and temp_file_to_clean.exists():
        temp_file_to_clean.unlink(missing_ok=True)

    if dry_run:
        message = (
            f"Dry Run Simulation: {import_result.problems_created} problems to create, "
            f"{import_result.problems_updated} to update, {import_result.images_created} images to extract, "
            f"{import_result.submissions_created} submissions, {import_result.ai_responses_created} AI notes, "
            f"{import_result.note_records_created} note records. "
            "No database changes were committed."
        )
    else:
        message = (
            f"Snapshot imported successfully: {import_result.problems_created} problems created, "
            f"{import_result.problems_updated} updated, {import_result.images_created} images extracted, "
            f"{import_result.submissions_created} submissions, {import_result.ai_responses_created} AI notes, "
            f"{import_result.note_records_created} note records."
        )

    if request.headers.get("HX-Request"):
        toast_html = render(
            request,
            "ui/partials/toast.html",
            {
                "status": "success",
                "message": message,
                "persistent": True,
            },
        ).content.decode("utf-8")
        return HttpResponse(toast_html)

    return redirect("ui:settings")


@require_POST
def reset_submissions_view(request):
    """Delete all user submissions and submission sync tasks while keeping problem solved status."""
    result = reset_user_submissions()
    message = (
        f"User submissions reset: removed {result['submissions_deleted']} submissions and "
        f"{result['tasks_deleted']} submission sync tasks. Problem solved statuses were kept."
    )
    if request.headers.get("HX-Request"):
        return render(
            request,
            "ui/partials/toast.html",
            {
                "status": "success",
                "message": message,
                "persistent": True,
            },
        )
    return redirect("ui:settings")


@require_POST
def clear_notes_records_view(request):
    """Delete NoteRecord synchronization rows from the database without touching markdown files on disk."""
    result = clear_note_records()
    message = (
        f"Cleared {result['note_records_deleted']} note tracking records from the database. "
        "Your note files in your Obsidian vault remain intact on disk."
    )
    if request.headers.get("HX-Request"):
        return render(
            request,
            "ui/partials/toast.html",
            {
                "status": "success",
                "message": message,
                "persistent": True,
            },
        )
    return redirect("ui:settings")


@require_POST
def reinit_database_view(request):
    """Wipe the SQLite database file and re-run all migrations for a completely fresh start."""
    reinit_database()
    if request.headers.get("HX-Request"):
        response = HttpResponse("")
        response["HX-Refresh"] = "true"
        return response
    return redirect("ui:settings")


@require_GET
def refresh_snapshots_view(request):
    """Refresh the list of snapshots in db_snapshots/ and return updated dropdown and list."""
    available_snapshots = list_snapshots()
    toast_message = f"Refreshed: {len(available_snapshots)} snapshot file(s) discovered in db_snapshots/."
    toast_html = render(
        request,
        "ui/partials/toast.html",
        {
            "status": "success",
            "message": toast_message,
            "persistent": True,
        },
    ).content.decode("utf-8")

    dropdown_html = render(
        request,
        "ui/partials/snapshot_dropdown.html",
        {
            "available_snapshots": available_snapshots,
            "oob": True,
        },
    ).content.decode("utf-8")

    snapshots_html = render(
        request,
        "ui/partials/snapshots_list.html",
        {
            "available_snapshots": available_snapshots,
            "oob": True,
        },
    ).content.decode("utf-8")

    return HttpResponse(toast_html + dropdown_html + snapshots_html)


@require_GET
def download_database_file_view(request):
    """Stream the raw SQLite database file as an attachment download."""
    db_file = Path(settings.BASE_DIR) / "db.sqlite3"
    if not db_file.exists():
        cfg_name = str(settings.DATABASES["default"]["NAME"])
        candidate = Path(cfg_name).resolve()
        if candidate.is_file():
            db_file = candidate
        else:
            raise Http404("Database file not found.")

    return FileResponse(
        open(db_file, "rb"),
        as_attachment=True,
        filename=db_file.name,
        content_type="application/x-sqlite3",
    )
