import json
import tempfile
from datetime import datetime
from pathlib import Path

import structlog
from django.conf import settings
from django.core.paginator import Paginator
from django.db import connection
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from problems.models import Problem, SyncTask
from problems.services.bundle import export_bundle, import_bundle
from problems.services.runner import start_background_sync
from problems.services.snapshots import (
    clear_note_records,
    list_snapshots,
    reinit_database,
    reset_user_submissions,
)
from problems.services.sync import mark_for_refetch
from prefill.providers.discovery import get_agy_models
from userconfig import AppConfig, load_config, save_config
from userconfig.paths import db_snapshots_dir

from .helpers.problems import (
    compute_facets,
    extract_selected_problem_ids,
    filter_problems,
    get_problem_base_queryset,
    parse_per_page,
    parse_problem_filters,
)
from .helpers.settings import (
    auto_set_timezone,
    get_settings_context,
    save_general_settings,
    save_leetcode_credentials,
    test_leetcode_connection,
)

logger = structlog.get_logger(__name__)

__all__ = [
    "compute_facets",
    "filter_problems",
    "matching_problem_ids",
    "problems_list",
    "refetch_selected_problems",
    "save_credentials_view",
    "save_general_settings_view",
    "set_timezone_view",
    "settings_view",
    "sync_selected_problems",
    "test_connection_view",
]


@require_GET
def problems_list(request):
    """Catalog browser for all LeetCode problems with search, filtering, and pagination."""
    filters = parse_problem_filters(request)
    per_page = parse_per_page(request)
    page_number = request.GET.get("page", 1)

    base_qs = get_problem_base_queryset()
    facets = compute_facets(base_qs, **filters)

    # Fetch problems matching active search and filters, ordered by leetcode_id,
    # with sync_tasks prefetched in a single batch to avoid N+1 queries for task badges.
    qs = (
        filter_problems(base_qs, **filters)
        .distinct()
        .order_by("leetcode_id")
        .prefetch_related("sync_tasks")
    )

    paginator = Paginator(qs, per_page)
    page_obj = paginator.get_page(page_number)

    active_filter_count = sum(bool(v) for k, v in filters.items() if k != "query")
    has_active_filters = bool(filters["query"]) or active_filter_count > 0

    context = {
        "active": "problems",
        "page_obj": page_obj,
        "per_page": per_page,
        "per_page_presets": [25, 50, 100, 200],
        "query": filters["query"],
        "difficulty": filters["difficulty"],
        "status": filters["status"],
        "sync_filter": filters["sync_filter"],
        "category": filters["category"],
        "notes_filter": filters["notes_filter"],
        "paid_filter": filters["paid_filter"],
        "difficulties": Problem.Difficulty.values,
        "categories": Problem.Category.values,
        "total_count": paginator.count,
        "facets": facets,
        "has_active_filters": has_active_filters,
        "active_filter_count": active_filter_count,
        "is_htmx": bool(request.headers.get("HX-Request")),
    }

    if request.headers.get("HX-Request"):
        return render(request, "ui/partials/problems_table.html", context)

    return render(request, "ui/problems.html", context)


@require_GET
def matching_problem_ids(request):
    """Return all problem IDs matching active search and filters for bulk operations."""
    filters = parse_problem_filters(request)
    base_qs = get_problem_base_queryset()
    qs = filter_problems(base_qs, **filters)

    # Fetch only primary keys ('id') of matching problems as a flat list
    ids = list(qs.values_list("id", flat=True))
    return JsonResponse({"ids": ids, "count": len(ids)})


@require_POST
def sync_selected_problems(request):
    """Trigger background sync for selected problems (automatically includes pending and failed tasks)."""
    try:
        problem_ids = extract_selected_problem_ids(request)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        return JsonResponse(
            {"status": "error", "message": f"Invalid request: {exc}"}, status=400
        )

    if not problem_ids:
        return JsonResponse(
            {"status": "error", "message": "No problems selected."}, status=400
        )

    statuses = [SyncTask.Status.PENDING, SyncTask.Status.FAILED]

    # Query the SyncTask table for the selected problems to find which ones actually
    # have pending or failed tasks that need to be processed (filters out already completed problems).
    runnable = list(
        SyncTask.objects.filter(problem_id__in=problem_ids, status__in=statuses)
        .values_list("problem_id", flat=True)
        .distinct()
    )

    if not runnable:
        return JsonResponse(
            {
                "status": "info",
                "message": f"Nothing to sync: all {len(problem_ids)} selected problem(s) are already synced.",
                "count": 0,
            }
        )

    if start_background_sync(runnable, retry_failed=True):
        return JsonResponse(
            {
                "status": "success",
                "message": f"Started syncing {len(runnable)} of {len(problem_ids)} selected problem(s) in the background. Refresh to see progress.",
                "count": len(runnable),
            }
        )
    else:
        return JsonResponse(
            {
                "status": "warning",
                "message": "A sync is already running in the background. Wait for it to finish.",
                "count": 0,
            }
        )


@require_POST
def refetch_selected_problems(request):
    """Reset selected problem sync tasks back to pending and launch background sync."""
    try:
        problem_ids = extract_selected_problem_ids(request)
        if request.content_type == "application/json":
            data = json.loads(request.body)
            part_str = str(data.get("part", "")).strip().lower()
        else:
            part_str = str(request.POST.get("part", "")).strip().lower()
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        return JsonResponse(
            {"status": "error", "message": f"Invalid request: {exc}"}, status=400
        )

    if not problem_ids:
        return JsonResponse(
            {"status": "error", "message": "No problems selected."}, status=400
        )

    # Map requested refetch action to the list of SyncTask parts to reset.
    # Note on cascade:
    # Resetting CONTENT automatically cascades to reset IMAGES as well (via REFETCH_CASCADE),
    # because new HTML invalidates all locally downloaded images.
    # Therefore, "all" only needs [CONTENT, SUBMISSIONS] to reset all 3 parts (content, images, submissions).
    PART_MAP: dict[str, list[SyncTask.Part]] = {
        "content_images": [
            SyncTask.Part.CONTENT
        ],  # Resets content + images (via cascade)
        "content": [SyncTask.Part.CONTENT],  # Alias for content + images
        "images": [SyncTask.Part.CONTENT],  # Images require re-scraping content first
        "submissions": [SyncTask.Part.SUBMISSIONS],  # Resets solution code task
        "all": [
            SyncTask.Part.CONTENT,
            SyncTask.Part.SUBMISSIONS,
        ],  # Resets content + images + submissions
    }

    if part_str not in PART_MAP:
        return JsonResponse(
            {"status": "error", "message": f"Invalid part '{part_str}'."}, status=400
        )

    parts_to_run = PART_MAP[part_str]

    total_reset = 0
    total_pinned = 0
    total_missing = 0

    for part in parts_to_run:
        res = mark_for_refetch(problem_ids, part)
        total_reset += res.tasks_reset
        total_pinned += res.pinned_skipped
        total_missing += res.missing

    sync_started = False
    if total_reset > 0:
        sync_started = start_background_sync(problem_ids, retry_failed=True)

    notes = [f"{total_reset} task(s) reset"]
    if total_pinned:
        notes.append(f"{total_pinned} skipped because solution is pinned")
    if total_missing:
        notes.append(f"{total_missing} had no matching task")

    if total_reset > 0:
        if sync_started:
            notes.append(
                f"Started background sync for {len(problem_ids)} problem(s). Refresh to see progress"
            )
            status = "success"
        else:
            notes.append(
                "A sync is already running in the background. Wait for it to finish"
            )
            status = "warning"
    else:
        status = "warning" if (total_pinned or total_missing) else "info"
        notes.append("No tasks needed resetting")

    return JsonResponse(
        {
            "status": status,
            "message": ". ".join(notes) + ".",
            "tasks_reset": total_reset,
            "sync_started": sync_started,
        }
    )


# --- Settings Page Views ---


@require_GET
def settings_view(request):
    """Render the application settings page."""
    context = get_settings_context()
    return render(request, "ui/settings.html", context)


@require_POST
def save_credentials_view(request):
    """Handle storing LeetCode session and csrf cookies, then auto-syncing account status."""
    session = request.POST.get("session", "").strip() or None
    csrf_token = request.POST.get("csrf_token", "").strip() or None

    ok, msg, details = save_leetcode_credentials(session=session, csrf_token=csrf_token)

    if request.headers.get("HX-Request"):
        preserved = details.get("preserved_previous", False)
        if ok:
            toast_status = "success"
        elif preserved:
            toast_status = "warning"
        else:
            toast_status = "error"

        # Stay until closed for preserved credentials warning or error
        is_persistent = bool(preserved or not ok)

        toast_html = render(
            request,
            "ui/partials/toast.html",
            {
                "status": toast_status,
                "message": msg,
                "persistent": is_persistent,
            },
        ).content.decode("utf-8")

        status_panel_html = render(
            request,
            "ui/partials/connection_status.html",
            {
                "conn": details,
                "credentials_configured": True,
                "leetcode_username": details.get("username"),
            },
        ).content.decode("utf-8")

        # Render floating toast and simultaneously update the connection status panel OOB
        response = HttpResponse(
            toast_html
            + f'<div id="connection-status-panel" hx-swap-oob="outerHTML">{status_panel_html}</div>'
        )
        if preserved:
            response["HX-Trigger"] = json.dumps({"clearCredentialsInputs": True})
        return response

    return JsonResponse({"ok": ok, "message": msg, "details": details})


@require_POST
def test_connection_view(request):
    """Check connectivity to LeetCode with the currently saved cookies."""
    res = test_leetcode_connection()
    if request.headers.get("HX-Request"):
        return render(request, "ui/partials/connection_status.html", {"conn": res})
    return JsonResponse(res)


@require_POST
def save_general_settings_view(request):
    """Save notes output directory, timezone, sync pacing, and AI preferences."""
    output_dir = request.POST.get("output_dir")
    create_directory = (
        request.POST.get("create_directory") in ("true", "on", "1")
        if ("output_dir" in request.POST or "create_directory" in request.POST)
        else None
    )
    time_zone = request.POST.get("time_zone")

    raw_ppm = request.POST.get("problems_per_minute")
    try:
        ppm = float(raw_ppm) if raw_ppm else None
    except ValueError, TypeError:
        ppm = None

    provider = request.POST.get("default_provider")
    review = (
        request.POST.get("review_before_writing") in ("true", "on", "1")
        if (
            "default_provider" in request.POST
            or "problems_per_minute" in request.POST
            or "review_before_writing" in request.POST
        )
        else None
    )

    # Claude Code settings
    claude_model_choice = request.POST.get("claude_model_choice")
    if claude_model_choice == "custom":
        claude_model = request.POST.get("claude_model_custom", "").strip() or "sonnet"
    elif claude_model_choice is not None:
        claude_model = claude_model_choice.strip()
    else:
        claude_model = None
    claude_effort = request.POST.get("claude_effort")

    # Antigravity CLI settings
    agy_model_choice = request.POST.get("agy_model_choice")
    if agy_model_choice == "custom":
        agy_model = request.POST.get("agy_model_custom", "").strip()
    elif agy_model_choice is not None:
        agy_model = agy_model_choice.strip()
    else:
        agy_model = None
    agy_effort = request.POST.get("agy_effort")

    ok, msg, details = save_general_settings(
        output_dir=output_dir,
        create_directory=create_directory,
        time_zone=time_zone,
        problems_per_minute=ppm,
        default_provider=provider,
        review_before_writing=review,
        claude_model=claude_model,
        claude_effort=claude_effort,
        agy_model=agy_model,
        agy_effort=agy_effort,
    )

    if request.headers.get("HX-Request"):
        alert_status = details.get("status", "success" if ok else "error")
        toast_html = render(
            request,
            "ui/partials/toast.html",
            {"status": alert_status, "message": msg},
        ).content.decode("utf-8")

        badge_html = render(
            request,
            "ui/partials/output_dir_badge.html",
            {
                "output_dir": details.get("output_dir", output_dir),
                "output_dir_exists": details.get("output_dir_exists", False),
                "oob": True,
            },
        ).content.decode("utf-8")

        # Render floating toast and simultaneously update the directory existence badge OOB
        return HttpResponse(toast_html + badge_html)

    return JsonResponse({"ok": ok, "message": msg, "details": details})


@require_POST
def set_timezone_view(request):
    """Auto-detect and store the client device timezone on first load."""
    try:
        data = json.loads(request.body)
        tz_name = data.get("timezone", "").strip()
    except ValueError, TypeError, json.JSONDecodeError:
        return JsonResponse({"status": "error", "message": "Invalid JSON"}, status=400)

    if not tz_name:
        return JsonResponse(
            {"status": "error", "message": "Missing timezone"}, status=400
        )

    ok, msg = auto_set_timezone(tz_name)
    return JsonResponse({"status": "ok" if ok else "skipped", "message": msg})


@require_POST
def refresh_agy_models_view(request):
    """Fetch the latest available models from Antigravity CLI and update the models dropdown."""
    models = get_agy_models(force_refresh=True)
    config = load_config()
    saved_agy = (config.providers or {}).get("agy", {})
    current_model = saved_agy.get("model", "")
    known_ids = {m["id"] for m in models}
    is_custom = bool(current_model and current_model not in known_ids)

    if models:
        status = "success"
        msg = f"Retrieved {len(models)} models from Antigravity CLI."
    else:
        status = "warning"
        msg = "Could not retrieve models from Antigravity CLI. You can enter a custom model name."

    if request.headers.get("HX-Request"):
        toast_html = render(
            request,
            "ui/partials/toast.html",
            {"status": status, "message": msg},
        ).content.decode("utf-8")

        dropdown_html = render(
            request,
            "ui/partials/agy_model_dropdown.html",
            {
                "agy_models": models,
                "agy_model": current_model,
                "agy_is_custom": is_custom,
                "oob": True,
            },
        ).content.decode("utf-8")

        return HttpResponse(toast_html + dropdown_html)

    return redirect("ui:settings")


@require_POST
def reset_settings_view(request):
    """Reset configuration to default values in data/config.json, leaving credentials in secrets.json untouched."""
    current_username = load_config().leetcode_username
    default_config = AppConfig(leetcode_username=current_username)
    save_config(default_config)

    if request.headers.get("HX-Request"):
        toast_html = render(
            request,
            "ui/partials/toast.html",
            {
                "status": "success",
                "message": "Preferences reset to default values. Saved credentials preserved.",
            },
        ).content.decode("utf-8")

        context = get_settings_context()
        context["oob"] = True
        cards_html = render(
            request,
            "ui/partials/configurable_settings.html",
            context,
        ).content.decode("utf-8")

        return HttpResponse(toast_html + cards_html)

    return redirect("ui:settings")


@require_POST
def export_snapshot_view(request):
    """Export problems, images, AI editorial notes, and optionally submissions or note records to a bundle archive."""
    destination = request.POST.get("destination", "folder")
    custom_filename = (request.POST.get("filename") or "").strip()
    export_all = request.POST.get("export_all") in ("true", "1", "on")

    include_submissions = (request.POST.get("include_submissions") in ("true", "1", "on")) or export_all
    include_ai_notes = (request.POST.get("include_ai_notes") in ("true", "1", "on")) or export_all
    include_note_records = (request.POST.get("include_note_records") in ("true", "1", "on")) or export_all
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
