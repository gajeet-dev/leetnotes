from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET

from ..helpers.problems import parse_per_page
from ..helpers.tasks import (
    get_algorithms_solved_problems,
    get_problems_batch_status,
)


def _render_catalog_table(request, extra_context=None):
    search_query = request.GET.get("q", "").strip() or request.POST.get("q", "").strip()
    difficulty = (
        request.GET.get("difficulty", "").strip()
        or request.POST.get("difficulty", "").strip()
    )
    status_filter = (
        request.GET.get("status", "").strip() or request.POST.get("status", "").strip()
    )
    page = request.GET.get("page", 1) or request.POST.get("page", 1)
    per_page = parse_per_page(request, default=100)

    data = get_algorithms_solved_problems(
        search_query=search_query,
        difficulty=difficulty,
        status_filter=status_filter,
        page=page,
        per_page=per_page,
    )

    context = {
        "active": "tasks",
        "search_query": search_query,
        "difficulty_filter": difficulty,
        "status_filter": status_filter,
        "per_page": per_page,
        "per_page_presets": [25, 50, 100, 200],
        **data,
    }
    if extra_context:
        context.update(extra_context)
    return render(request, "ui/partials/tasks_new_table.html", context)


@require_GET
def tasks_catalog_view(request):
    """Problem Selection Catalogue: browse solved algorithms and select problems for notes generation."""
    if request.headers.get("HX-Request"):
        return _render_catalog_table(request)

    search_query = request.GET.get("q", "").strip()
    difficulty = request.GET.get("difficulty", "").strip()
    status_filter = request.GET.get("status", "").strip()
    page = request.GET.get("page", 1)
    per_page = parse_per_page(request, default=100)

    data = get_algorithms_solved_problems(
        search_query=search_query,
        difficulty=difficulty,
        status_filter=status_filter,
        page=page,
        per_page=per_page,
    )

    context = {
        "active": "tasks",
        "search_query": search_query,
        "difficulty_filter": difficulty,
        "status_filter": status_filter,
        "per_page": per_page,
        "per_page_presets": [25, 50, 100, 200],
        **data,
    }
    return render(request, "ui/tasks_new.html", context)


# Route alias
tasks_new_view = tasks_catalog_view


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
    except Exception:
        return JsonResponse({"statuses": {}})

    if not problem_pks:
        return JsonResponse({"statuses": {}})

    statuses = get_problems_batch_status(problem_pks)
    return JsonResponse({"statuses": statuses})
