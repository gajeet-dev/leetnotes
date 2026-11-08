import json

import structlog
from django.core.paginator import Paginator
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from problems.models import Problem, SyncTask
from problems.services.runner import start_background_sync
from problems.services.sync import mark_for_refetch
from userconfig import AppConfig, load_config, save_config

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
        toast_html = render(
            request,
            "ui/partials/toast.html",
            {"status": "success" if ok else "error", "message": msg},
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
        return HttpResponse(
            toast_html
            + f'<div id="connection-status-panel" hx-swap-oob="outerHTML">{status_panel_html}</div>'
        )

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
    create_directory = request.POST.get("create_directory") in ("true", "on", "1")
    time_zone = request.POST.get("time_zone")

    raw_ppm = request.POST.get("problems_per_minute")
    try:
        ppm = float(raw_ppm) if raw_ppm else None
    except (ValueError, TypeError):
        ppm = None

    provider = request.POST.get("default_provider")
    review = request.POST.get("review_before_writing") in ("true", "on", "1")

    ok, msg, details = save_general_settings(
        output_dir=output_dir,
        create_directory=create_directory,
        time_zone=time_zone,
        problems_per_minute=ppm,
        default_provider=provider,
        review_before_writing=review,
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
    except (ValueError, TypeError, json.JSONDecodeError):
        return JsonResponse({"status": "error", "message": "Invalid JSON"}, status=400)

    if not tz_name:
        return JsonResponse({"status": "error", "message": "Missing timezone"}, status=400)

    ok, msg = auto_set_timezone(tz_name)
    return JsonResponse({"status": "ok" if ok else "skipped", "message": msg})


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
