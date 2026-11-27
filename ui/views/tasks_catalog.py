import structlog
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET
from pydantic import ValidationError

from pipeline.models import PipelineItem
from pipeline.tasks.eligibility import busy_problem_ids
from problems.models import Problem
from ui.dto import ProblemCatalogDTO

from ..helpers.problems import PER_PAGE_PRESETS
from ..helpers.tasks import (
    get_problems_batch_status,
    paginate_recent_and_other_catalog,
    paginate_search_catalog,
    resolve_problems_note_status,
)

logger = structlog.get_logger(__name__)


@require_GET
def tasks_catalog_view(request):
    """Problem Selection Catalogue: browse solved algorithms and select problems for notes generation."""
    try:
        dto = ProblemCatalogDTO.model_validate(request.GET.dict())
    except ValidationError:
        return HttpResponseBadRequest("Invalid filter parameters")

    search_query = dto.q
    difficulty = dto.difficulty.value if dto.difficulty else ""
    status_filter = dto.status
    page_num = dto.page
    per_page = dto.per_page

    qs = Problem.objects.solved().algorithms().with_submissions()

    if difficulty:
        qs = qs.filter_difficulty(difficulty)

    if search_query:
        page_data = paginate_search_catalog(qs, search_query, page_num, per_page)
    else:
        page_data = paginate_recent_and_other_catalog(
            qs, difficulty, page_num, per_page
        )

    all_visible = page_data["recent_problems"] + page_data["other_problems"]
    busy_ids = busy_problem_ids(all_visible)
    statuses_by_pk, latest_items = resolve_problems_note_status(
        all_visible, include_pipeline=True
    )

    for p in all_visible:
        p.is_busy = p.pk in busy_ids
        if not getattr(p, "submission_date", None):
            p.submission_date = (
                p.submission.submission_date
                if hasattr(p, "submission") and p.submission
                else None
            )
        p.pipeline_item = latest_items.get(p.pk)
        p.note_status_dto = statuses_by_pk[p.pk]
        p.note_saved = p.note_status_dto.note_saved

    has_active_items = any(
        p.pipeline_item
        and p.pipeline_item.status
        in (
            PipelineItem.Status.QUEUED,
            PipelineItem.Status.RUNNING,
            PipelineItem.Status.NEEDS_INPUT,
        )
        for p in all_visible
    )

    context = {
        "active": "tasks",
        "search_query": search_query,
        "difficulty_filter": difficulty,
        "status_filter": status_filter,
        "per_page": per_page,
        "per_page_presets": list(PER_PAGE_PRESETS),
        "busy_ids": busy_ids,
        "is_search": bool(search_query),
        "has_active_items": has_active_items,
        **page_data,
    }

    template = (
        "ui/partials/tasks_new_table.html"
        if request.headers.get("HX-Request")
        else "ui/tasks_new.html"
    )
    return render(request, template, context)


@require_GET
def tasks_status_batch_view(request):
    """Return JSON status of problems for Catalogue polling (note saved, active pipeline status).

    Avoids fetching LeetCode API or reloading the entire table.
    """
    problem_ids_param = request.GET.get("problem_ids", "").strip()
    if not problem_ids_param:
        return JsonResponse({"statuses": {}})

    try:
        problem_pks = [
            int(pk_str.strip())
            for pk_str in problem_ids_param.split(",")
            if pk_str.strip().isdigit()
        ]
    except ValueError, TypeError:
        return JsonResponse({"statuses": {}})

    if not problem_pks:
        return JsonResponse({"statuses": {}})

    statuses = get_problems_batch_status(problem_pks)
    return JsonResponse({"statuses": statuses})
