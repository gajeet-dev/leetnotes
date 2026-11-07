import structlog
from django.core.paginator import Paginator
from django.db.models import Exists, OuterRef, Q
from django.shortcuts import render
from django.views.decorators.http import require_GET

from notes.models import NoteRecord
from pipeline.tasks.eligibility import busy_problem_ids
from prefill.models import AIResponse
from problems.models import Problem

logger = structlog.get_logger(__name__)


@require_GET
def problems_list(request):
    """Catalog browser for all LeetCode problems with search, filtering, and pagination."""
    query = request.GET.get("q", "").strip()
    difficulty = request.GET.get("difficulty", "").strip()
    status = request.GET.get("status", "").strip()
    category = request.GET.get("category", "").strip()
    notes_filter = request.GET.get("notes", "").strip()
    page_number = request.GET.get("page", 1)

    has_note_subquery = NoteRecord.objects.filter(problem_id=OuterRef("pk"))
    has_draft_subquery = AIResponse.objects.filter(
        problem_id=OuterRef("pk"), is_active=True, status=AIResponse.Status.VALID
    )

    qs = Problem.objects.all().annotate(
        has_note=Exists(has_note_subquery),
        has_draft=Exists(has_draft_subquery),
    )

    if query:
        if query.isdigit():
            qs = qs.filter(
                Q(leetcode_id=int(query))
                | Q(title__icontains=query)
                | Q(slug__icontains=query)
            )
        else:
            qs = qs.filter(Q(title__icontains=query) | Q(slug__icontains=query))

    if difficulty:
        qs = qs.filter(difficulty=difficulty)

    if status == "solved":
        qs = qs.filter(solved=True)
    elif status == "unsolved":
        qs = qs.filter(solved=False)

    if category:
        qs = qs.filter(category=category)

    if notes_filter == "has_note":
        qs = qs.filter(has_note=True)
    elif notes_filter == "has_draft":
        qs = qs.filter(has_draft=True, has_note=False)
    elif notes_filter == "no_note":
        qs = qs.filter(has_note=False, has_draft=False)

    qs = qs.order_by("leetcode_id")

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(page_number)
    busy_ids = busy_problem_ids(page_obj.object_list)

    context = {
        "active": "problems",
        "page_obj": page_obj,
        "query": query,
        "difficulty": difficulty,
        "status": status,
        "category": category,
        "notes_filter": notes_filter,
        "difficulties": Problem.Difficulty.values,
        "categories": Problem.Category.values,
        "busy_ids": busy_ids,
        "total_count": paginator.count,
    }

    if request.headers.get("HX-Request"):
        return render(request, "ui/partials/problems_table.html", context)

    return render(request, "ui/problems.html", context)
