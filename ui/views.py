import json

import structlog
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from problems.models import Problem, SyncTask
from problems.services.runner import start_background_sync
from problems.services.sync import mark_for_refetch

from .helpers.problems import (
    compute_facets,
    extract_selected_problem_ids,
    filter_problems,
    get_problem_base_queryset,
    parse_per_page,
    parse_problem_filters,
)

logger = structlog.get_logger(__name__)

__all__ = [
    "compute_facets",
    "filter_problems",
    "matching_problem_ids",
    "problems_list",
    "refetch_selected_problems",
    "sync_selected_problems",
]


@require_GET
def problems_list(request):
    """Catalog browser for all LeetCode problems with search, filtering, and pagination."""
    filters = parse_problem_filters(request)
    per_page = parse_per_page(request)
    page_number = request.GET.get("page", 1)

    base_qs = get_problem_base_queryset()
    facets = compute_facets(base_qs, **filters)

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

    valid_parts = {
        "content": SyncTask.Part.CONTENT,
        "content_images": SyncTask.Part.CONTENT,
        "submissions": SyncTask.Part.SUBMISSIONS,
        "images": SyncTask.Part.CONTENT,
    }

    if part_str == "all":
        parts_to_run = [SyncTask.Part.CONTENT, SyncTask.Part.SUBMISSIONS]
    elif part_str in valid_parts:
        parts_to_run = [valid_parts[part_str]]
    else:
        return JsonResponse(
            {"status": "error", "message": f"Invalid part '{part_str}'."}, status=400
        )

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
