import json
import structlog
from django.core.paginator import Paginator
from django.db.models import Count, Exists, OuterRef, Q
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from notes.models import NoteRecord
from pipeline.tasks.eligibility import busy_problem_ids
from prefill.models import AIResponse
from problems.models import Problem, SyncTask
from problems.services.runner import start_background_sync
from problems.services.sync import mark_for_refetch

logger = structlog.get_logger(__name__)


def filter_problems(
    qs,
    *,
    query="",
    difficulty="",
    status="",
    sync_filter="",
    category="",
    notes_filter="",
    paid_filter="",
    exclude_field=None,
):
    """Apply catalog browser filters to a Problem queryset."""
    if query and exclude_field != "query":
        if query.isdigit():
            qs = qs.filter(
                Q(leetcode_id=int(query))
                | Q(title__icontains=query)
                | Q(slug__icontains=query)
            )
        else:
            qs = qs.filter(Q(title__icontains=query) | Q(slug__icontains=query))

    if difficulty and exclude_field != "difficulty":
        qs = qs.filter(difficulty=difficulty)

    if status and exclude_field != "status":
        if status == "solved":
            qs = qs.filter(solved=True)
        elif status == "unsolved":
            qs = qs.filter(solved=False)

    if paid_filter and exclude_field != "paid":
        if paid_filter in ("paid", "true"):
            qs = qs.filter(paid_only=True)
        elif paid_filter in ("free", "unpaid", "false"):
            qs = qs.filter(paid_only=False)

    if sync_filter and exclude_field != "sync":
        if sync_filter == "synced":
            qs = (
                qs.filter(sync_tasks__isnull=False)
                .exclude(sync_tasks__status=SyncTask.Status.PENDING)
                .exclude(sync_tasks__status=SyncTask.Status.FAILED)
            )
        elif sync_filter == "pending":
            qs = qs.filter(sync_tasks__status=SyncTask.Status.PENDING)
        elif sync_filter == "failed":
            qs = qs.filter(sync_tasks__status=SyncTask.Status.FAILED)
        elif sync_filter == "none":
            qs = qs.filter(sync_tasks__isnull=True)

    if category and exclude_field != "category":
        if category in ("None", "none", "null"):
            qs = qs.filter(Q(category__isnull=True) | Q(category=""))
        else:
            qs = qs.filter(category=category)

    if notes_filter and exclude_field != "notes":
        if notes_filter == "has_note":
            qs = qs.filter(has_note=True)
        elif notes_filter == "has_draft":
            qs = qs.filter(has_draft=True, has_note=False)
        elif notes_filter == "no_note":
            qs = qs.filter(has_note=False, has_draft=False)

    return qs


def compute_facets(
    base_qs,
    *,
    query="",
    difficulty="",
    status="",
    sync_filter="",
    category="",
    notes_filter="",
    paid_filter="",
):
    """Compute reactive faceted counts across all filter dimensions."""

    # 1. Difficulty facets (exclude difficulty, apply all other filters)
    diff_qs = filter_problems(
        base_qs,
        query=query,
        difficulty=difficulty,
        status=status,
        sync_filter=sync_filter,
        category=category,
        notes_filter=notes_filter,
        paid_filter=paid_filter,
        exclude_field="difficulty",
    )
    diff_counts = dict(
        diff_qs.values_list("difficulty").annotate(c=Count("id", distinct=True))
    )
    easy_count = diff_counts.get(Problem.Difficulty.EASY, 0)
    medium_count = diff_counts.get(Problem.Difficulty.MEDIUM, 0)
    hard_count = diff_counts.get(Problem.Difficulty.HARD, 0)

    # 2. Status facets (exclude status, apply all other filters)
    status_qs = filter_problems(
        base_qs,
        query=query,
        difficulty=difficulty,
        status=status,
        sync_filter=sync_filter,
        category=category,
        notes_filter=notes_filter,
        paid_filter=paid_filter,
        exclude_field="status",
    )
    status_counts = dict(
        status_qs.values_list("solved").annotate(c=Count("id", distinct=True))
    )
    solved_count = status_counts.get(True, 0)
    unsolved_count = status_counts.get(False, 0)

    # 3. Sync facets (exclude sync, apply all other filters)
    sync_qs = filter_problems(
        base_qs,
        query=query,
        difficulty=difficulty,
        status=status,
        sync_filter=sync_filter,
        category=category,
        notes_filter=notes_filter,
        paid_filter=paid_filter,
        exclude_field="sync",
    )
    synced_count = (
        sync_qs.filter(sync_tasks__isnull=False)
        .exclude(sync_tasks__status=SyncTask.Status.PENDING)
        .exclude(sync_tasks__status=SyncTask.Status.FAILED)
        .values("id")
        .distinct()
        .count()
    )
    pending_sync_count = (
        sync_qs.filter(sync_tasks__status=SyncTask.Status.PENDING)
        .values("id")
        .distinct()
        .count()
    )
    failed_sync_count = (
        sync_qs.filter(sync_tasks__status=SyncTask.Status.FAILED)
        .values("id")
        .distinct()
        .count()
    )
    none_sync_count = (
        sync_qs.filter(sync_tasks__isnull=True).values("id").distinct().count()
    )

    # 4. Notes facets (exclude notes, apply all other filters)
    notes_qs = filter_problems(
        base_qs,
        query=query,
        difficulty=difficulty,
        status=status,
        sync_filter=sync_filter,
        category=category,
        notes_filter=notes_filter,
        paid_filter=paid_filter,
        exclude_field="notes",
    )
    notes_saved = notes_qs.filter(has_note=True).values("id").distinct().count()
    notes_draft = (
        notes_qs.filter(has_draft=True, has_note=False).values("id").distinct().count()
    )
    notes_none = (
        notes_qs.filter(has_note=False, has_draft=False).values("id").distinct().count()
    )

    # 5. Category facets (exclude category, apply all other filters)
    cat_qs = filter_problems(
        base_qs,
        query=query,
        difficulty=difficulty,
        status=status,
        sync_filter=sync_filter,
        category=category,
        notes_filter=notes_filter,
        paid_filter=paid_filter,
        exclude_field="category",
    )
    raw_cat_counts = dict(
        cat_qs.values_list("category").annotate(c=Count("id", distinct=True))
    )
    category_counts = {
        cat: raw_cat_counts.get(cat, 0) for cat in Problem.Category.values
    }
    category_counts["None"] = (
        raw_cat_counts.get(None, 0) + raw_cat_counts.get("", 0)
    )

    # 6. Paid/Free facets (exclude paid, apply all other filters)
    paid_qs = filter_problems(
        base_qs,
        query=query,
        difficulty=difficulty,
        status=status,
        sync_filter=sync_filter,
        category=category,
        notes_filter=notes_filter,
        paid_filter=paid_filter,
        exclude_field="paid",
    )
    paid_counts = dict(
        paid_qs.values_list("paid_only").annotate(c=Count("id", distinct=True))
    )
    paid_count = paid_counts.get(True, 0)
    free_count = paid_counts.get(False, 0)
    total_paid = sum(paid_counts.values())
    total_difficulty = sum(diff_counts.values())
    total_status = sum(status_counts.values())
    total_sync = (
        synced_count + pending_sync_count + failed_sync_count + none_sync_count
    )
    total_notes = notes_saved + notes_draft + notes_none
    total_category = sum(raw_cat_counts.values())

    return {
        "easy_count": easy_count,
        "medium_count": medium_count,
        "hard_count": hard_count,
        "solved_count": solved_count,
        "unsolved_count": unsolved_count,
        "synced_count": synced_count,
        "pending_sync_count": pending_sync_count,
        "failed_sync_count": failed_sync_count,
        "none_sync_count": none_sync_count,
        "notes_saved": notes_saved,
        "notes_draft": notes_draft,
        "notes_none": notes_none,
        "category_counts": category_counts,
        "paid_count": paid_count,
        "free_count": free_count,
        "total_paid": total_paid,
        "total_difficulty": total_difficulty,
        "total_status": total_status,
        "total_sync": total_sync,
        "total_notes": total_notes,
        "total_category": total_category,
    }


@require_GET
def problems_list(request):
    """Catalog browser for all LeetCode problems with search, filtering, and pagination."""
    query = request.GET.get("q", "").strip()
    difficulty = request.GET.get("difficulty", "").strip()
    status = request.GET.get("status", "").strip()
    sync_filter = request.GET.get("sync", "").strip()
    category = request.GET.get("category", "").strip()
    if category.lower() in ("none", "null"):
        category = "None"
    notes_filter = request.GET.get("notes", "").strip()
    paid_filter = request.GET.get("paid", "").strip()
    page_number = request.GET.get("page", 1)

    has_note_subquery = NoteRecord.objects.filter(problem_id=OuterRef("pk"))
    has_draft_subquery = AIResponse.objects.filter(
        problem_id=OuterRef("pk"), is_active=True, status=AIResponse.Status.VALID
    )

    base_qs = Problem.objects.all().annotate(
        has_note=Exists(has_note_subquery),
        has_draft=Exists(has_draft_subquery),
        total_sync_count=Count("sync_tasks"),
        pending_sync_count=Count(
            "sync_tasks", filter=Q(sync_tasks__status=SyncTask.Status.PENDING)
        ),
        failed_sync_count=Count(
            "sync_tasks", filter=Q(sync_tasks__status=SyncTask.Status.FAILED)
        ),
    )

    facets = compute_facets(
        base_qs,
        query=query,
        difficulty=difficulty,
        status=status,
        sync_filter=sync_filter,
        category=category,
        notes_filter=notes_filter,
        paid_filter=paid_filter,
    )

    qs = filter_problems(
        base_qs,
        query=query,
        difficulty=difficulty,
        status=status,
        sync_filter=sync_filter,
        category=category,
        notes_filter=notes_filter,
        paid_filter=paid_filter,
    ).distinct()

    qs = qs.order_by("leetcode_id").prefetch_related("sync_tasks")

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(page_number)
    busy_ids = busy_problem_ids(page_obj.object_list)

    active_filter_count = sum(
        bool(v)
        for v in [difficulty, status, sync_filter, category, notes_filter, paid_filter]
    )
    has_active_filters = bool(query) or active_filter_count > 0

    context = {
        "active": "problems",
        "page_obj": page_obj,
        "query": query,
        "difficulty": difficulty,
        "status": status,
        "sync_filter": sync_filter,
        "category": category,
        "notes_filter": notes_filter,
        "paid_filter": paid_filter,
        "difficulties": Problem.Difficulty.values,
        "categories": Problem.Category.values,
        "busy_ids": busy_ids,
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
    query = request.GET.get("q", "").strip()
    difficulty = request.GET.get("difficulty", "").strip()
    status = request.GET.get("status", "").strip()
    sync_filter = request.GET.get("sync", "").strip()
    category = request.GET.get("category", "").strip()
    if category.lower() in ("none", "null"):
        category = "None"
    notes_filter = request.GET.get("notes", "").strip()
    paid_filter = request.GET.get("paid", "").strip()

    has_note_subquery = NoteRecord.objects.filter(problem_id=OuterRef("pk"))
    has_draft_subquery = AIResponse.objects.filter(
        problem_id=OuterRef("pk"), is_active=True, status=AIResponse.Status.VALID
    )

    base_qs = Problem.objects.all().annotate(
        has_note=Exists(has_note_subquery),
        has_draft=Exists(has_draft_subquery),
    )

    qs = filter_problems(
        base_qs,
        query=query,
        difficulty=difficulty,
        status=status,
        sync_filter=sync_filter,
        category=category,
        notes_filter=notes_filter,
        paid_filter=paid_filter,
    )

    ids = list(qs.values_list("id", flat=True))
    return JsonResponse({"ids": ids, "count": len(ids)})


@require_POST
def sync_selected_problems(request):
    """Trigger background sync for selected problems (automatically includes pending and failed tasks)."""
    try:
        if request.content_type == "application/json":
            data = json.loads(request.body)
            raw_ids = data.get("problem_ids", [])
        else:
            raw_ids = request.POST.getlist("problem_ids")
            if not raw_ids and "problem_ids" in request.POST:
                raw_ids = [x.strip() for x in request.POST.get("problem_ids", "").split(",") if x.strip()]
        problem_ids = [int(x) for x in raw_ids if str(x).isdigit()]
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        return JsonResponse({"status": "error", "message": f"Invalid request: {exc}"}, status=400)

    if not problem_ids:
        return JsonResponse({"status": "error", "message": "No problems selected."}, status=400)

    statuses = [SyncTask.Status.PENDING, SyncTask.Status.FAILED]

    runnable = list(
        SyncTask.objects.filter(problem_id__in=problem_ids, status__in=statuses)
        .values_list("problem_id", flat=True)
        .distinct()
    )

    if not runnable:
        return JsonResponse({
            "status": "info",
            "message": f"Nothing to sync: all {len(problem_ids)} selected problem(s) are already synced.",
            "count": 0,
        })

    if start_background_sync(runnable, retry_failed=True):
        return JsonResponse({
            "status": "success",
            "message": f"Started syncing {len(runnable)} of {len(problem_ids)} selected problem(s) in the background. Refresh to see progress.",
            "count": len(runnable),
        })
    else:
        return JsonResponse({
            "status": "warning",
            "message": "A sync is already running in the background. Wait for it to finish.",
            "count": 0,
        })


@require_POST
def refetch_selected_problems(request):
    """Reset selected problem sync tasks back to pending for re-fetching."""
    try:
        if request.content_type == "application/json":
            data = json.loads(request.body)
            raw_ids = data.get("problem_ids", [])
            part_str = str(data.get("part", "")).strip().lower()
        else:
            raw_ids = request.POST.getlist("problem_ids")
            if not raw_ids and "problem_ids" in request.POST:
                raw_ids = [x.strip() for x in request.POST.get("problem_ids", "").split(",") if x.strip()]
            part_str = str(request.POST.get("part", "")).strip().lower()
        problem_ids = [int(x) for x in raw_ids if str(x).isdigit()]
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        return JsonResponse({"status": "error", "message": f"Invalid request: {exc}"}, status=400)

    if not problem_ids:
        return JsonResponse({"status": "error", "message": "No problems selected."}, status=400)

    valid_parts = {
        "content": SyncTask.Part.CONTENT,
        "submissions": SyncTask.Part.SUBMISSIONS,
        "images": SyncTask.Part.IMAGES,
    }

    if part_str == "all":
        parts_to_run = [SyncTask.Part.CONTENT, SyncTask.Part.SUBMISSIONS]
    elif part_str in valid_parts:
        parts_to_run = [valid_parts[part_str]]
    else:
        return JsonResponse({"status": "error", "message": f"Invalid part '{part_str}'."}, status=400)

    total_reset = 0
    total_pinned = 0
    total_missing = 0

    for part in parts_to_run:
        res = mark_for_refetch(problem_ids, part)
        total_reset += res.tasks_reset
        total_pinned += res.pinned_skipped
        total_missing += res.missing

    notes = [f"{total_reset} task(s) reset to pending"]
    if total_pinned:
        notes.append(f"{total_pinned} skipped because solution is pinned")
    if total_missing:
        notes.append(f"{total_missing} had no matching task")

    status = "warning" if (total_pinned or total_missing) and not total_reset else "success"
    return JsonResponse({
        "status": status,
        "message": ". ".join(notes) + ". Now run 'Sync Selected' to fetch them.",
        "tasks_reset": total_reset,
    })
