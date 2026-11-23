import math

import structlog
from django.core.paginator import Paginator
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET
from pydantic import ValidationError

from notes.models import NoteRecord
from notes.state import NoteState, classify_note, dir_key
from pipeline.models import PipelineItem
from pipeline.tasks.eligibility import busy_problem_ids
from problems.models import Problem
from problems.services.submissions import (
    fetch_recent_submissions_with_db_updates,
)
from ui.dto import ProblemCatalogDTO
from userconfig import effective

from ..helpers.paginator import CustomPage
from ..helpers.tasks import (
    get_problems_batch_status,
)

logger = structlog.get_logger(__name__)


@require_GET
def tasks_catalog_view(request, extra_context=None):
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

    if search_query:
        qs = qs.search(search_query)

    if difficulty:
        qs = qs.filter_difficulty(difficulty)

    if search_query:
        # Search active: show matching in a single paginated list
        recent_problems = []
        recent_total = 0
        paginator = Paginator(qs.order_by("leetcode_id"), per_page)
        page_obj = paginator.get_page(page_num)
        other_problems = list(page_obj.object_list)
        other_total = paginator.count
        total_count = other_total

        if total_count == 0:
            display_start = 0
            display_end = 0
        else:
            display_start = page_obj.start_index()
            display_end = page_obj.end_index()
    else:
        # Fetch live recent accepted submissions from the LeetCode API without caching
        recent_items = fetch_recent_submissions_with_db_updates()

        seen_slugs = set()
        ordered_recent_slugs = []
        recent_timestamps = {}
        for item in recent_items:
            slug = item.slug
            ts = item.timestamp
            if slug and slug not in seen_slugs:
                seen_slugs.add(slug)
                ordered_recent_slugs.append(slug)
                recent_timestamps[slug] = ts

        db_problems_by_slug = {
            p.slug: p
            for p in Problem.objects.filter(
                slug__in=ordered_recent_slugs
            ).with_submissions()
        }

        # Filter strictly by category: only keep problems from the Algorithms category
        recent_algo_problems = []
        recent_algo_pks = set()
        for slug in ordered_recent_slugs:
            p = db_problems_by_slug.get(slug)
            if p is None:
                continue

            if p.category != Problem.Category.ALGORITHMS:
                continue

            if not p.solved:
                p.solved = True
                p.save(update_fields=["solved", "updated_at"])

            recent_algo_problems.append(p)
            recent_algo_pks.add(p.pk)

        # Apply difficulty filter if specified
        recent_list = []
        for p in recent_algo_problems:
            if difficulty and p.difficulty.lower() != difficulty.lower():
                continue
            if recent_timestamps.get(p.slug):
                p.submission_date = recent_timestamps[p.slug]
            recent_list.append(p)

        recent_total = len(recent_list)

        # Other problems matching current filters
        other_qs = qs.exclude(pk__in=recent_algo_pks).order_by("leetcode_id")
        other_total = other_qs.count()
        total_count = recent_total + other_total

        page_1_other_count = min(max(0, per_page - recent_total), other_total)
        remaining_other_count = other_total - page_1_other_count

        if remaining_other_count > 0:
            subsequent_pages = math.ceil(remaining_other_count / per_page)
            num_pages = 1 + subsequent_pages
        else:
            num_pages = 1

        page_num = max(1, min(page_num, num_pages))

        if page_num == 1:
            recent_problems = recent_list
            if page_1_other_count > 0:
                other_problems = list(other_qs[:page_1_other_count])
            else:
                other_problems = []

            if total_count == 0:
                display_start = 0
                display_end = 0
            else:
                display_start = 1
                display_end = len(recent_problems) + len(other_problems)
        else:
            recent_problems = []
            start_offset = page_1_other_count + (page_num - 2) * per_page
            end_offset = start_offset + per_page
            other_problems = list(other_qs[start_offset:end_offset])

            display_start = recent_total + start_offset + 1
            display_end = min(total_count, display_start + len(other_problems) - 1)

        page_obj = CustomPage(
            object_list=other_problems
            if page_num > 1
            else (recent_problems + other_problems),
            number=page_num,
            num_pages=num_pages,
            total_count=total_count,
        )

    all_visible = recent_problems + other_problems
    busy_ids = busy_problem_ids(all_visible)

    # Output directory verification for NoteRecord
    output_folders = effective.output_dirs()
    target_output_dir = str(output_folders[0]) if output_folders else None

    note_records_by_problem: dict[int, list[NoteRecord]] = {}
    if target_output_dir and all_visible:
        canonical_key = dir_key(target_output_dir)
        records = NoteRecord.objects.filter(
            problem__in=all_visible,
            output_dir=canonical_key,
        )
        for r in records:
            note_records_by_problem.setdefault(r.problem_id, []).append(r)

    # Attach pipeline item and verified note status to each visible problem
    latest_items: dict[int, PipelineItem] = {}
    if all_visible:
        for item in PipelineItem.objects.filter(problem__in=all_visible).order_by(
            "-created_at"
        ):
            if item.problem_id not in latest_items:
                latest_items[item.problem_id] = item

    for p in all_visible:
        p.is_busy = p.pk in busy_ids
        if not getattr(p, "submission_date", None):
            p.submission_date = (
                p.submission.submission_date
                if hasattr(p, "submission") and p.submission
                else None
            )
        p.pipeline_item = latest_items.get(p.pk)

        # Note existence verified against disk:
        p.note_saved = False
        recs = note_records_by_problem.get(p.pk, [])
        for r in recs:
            state = classify_note(r.content_hash, r.full_path)
            if state != NoteState.MISSING and r.full_path.exists():
                p.note_saved = True
                break

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
        "per_page_presets": [25, 50, 100, 200],
        "page_obj": page_obj,
        "recent_problems": recent_problems,
        "other_problems": other_problems,
        "recent_total": recent_total,
        "other_total": other_total,
        "total_count": total_count,
        "display_start": display_start,
        "display_end": display_end,
        "busy_ids": busy_ids,
        "is_search": bool(search_query),
        "has_active_items": has_active_items,
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
