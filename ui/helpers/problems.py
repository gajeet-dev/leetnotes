import json

from django.db.models import Count, Exists, OuterRef, Q

from notes.models import NoteRecord
from prefill.models import AIResponse
from problems.models import Problem, SyncTask


def parse_problem_filters(request) -> dict[str, str]:
    """Extract and normalize problem catalog filter parameters from request.GET."""
    category = request.GET.get("category", "").strip()
    if category.lower() in ("none", "null"):
        category = "None"

    return {
        "query": request.GET.get("q", "").strip(),
        "difficulty": request.GET.get("difficulty", "").strip(),
        "status": request.GET.get("status", "").strip(),
        "sync_filter": request.GET.get("sync", "").strip(),
        "category": category,
        "notes_filter": request.GET.get("notes", "").strip(),
        "paid_filter": request.GET.get("paid", "").strip(),
    }


def get_problem_base_queryset():
    """Return base queryset for problems annotated with note existence and sync counts."""
    has_note_subquery = NoteRecord.objects.filter(problem_id=OuterRef("pk"))
    has_draft_subquery = AIResponse.objects.filter(
        problem_id=OuterRef("pk"), is_active=True, status=AIResponse.Status.VALID
    )

    return Problem.objects.all().annotate(
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


def extract_selected_problem_ids(request) -> list[int]:
    """Parse problem IDs from either JSON body or form POST data."""
    if request.content_type == "application/json":
        data = json.loads(request.body)
        raw_ids = data.get("problem_ids", [])
    else:
        raw_ids = request.POST.getlist("problem_ids")
        if not raw_ids and "problem_ids" in request.POST:
            raw_ids = [
                x.strip()
                for x in request.POST.get("problem_ids", "").split(",")
                if x.strip()
            ]
    return [int(x) for x in raw_ids if str(x).isdigit()]


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
    category_counts["None"] = raw_cat_counts.get(None, 0) + raw_cat_counts.get("", 0)

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
    total_sync = synced_count + pending_sync_count + failed_sync_count + none_sync_count
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
