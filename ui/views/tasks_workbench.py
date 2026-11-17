from typing import Any

import structlog
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import (
    require_GET,
    require_http_methods,
    require_POST,
)

from pipeline.actions import (
    approve_all_reviews,
    approve_review,
    delete_ai_response,
    reject_review,
    resolve_submission_choice,
    retry_failed_item,
    select_existing_prefill,
    submit_manual_reply,
    switch_provider_and_generate,
)
from pipeline.models import PipelineItem
from pipeline.stages.definitions import Stage
from pipeline.tasks.control import (
    cancel as cancel_item,
    pause as pause_item,
    resume as resume_item,
    start_pipeline,
)
from prefill.models import AIResponse
from prefill.providers.config import default_provider_name, provider_names
from problems.models import Problem
from userconfig import load_config

from ..helpers.problems import parse_per_page
from ..helpers.tasks import (
    get_modal_data_for_item_stage,
    get_workbench_pipeline_items,
    render_ai_response_to_html,
)

logger = structlog.get_logger(__name__)


def _render_workbench_table(request, extra_context=None):
    search_query = request.GET.get("q", "").strip() or request.POST.get("q", "").strip()
    status_filter = (
        request.GET.get("status", "").strip() or request.POST.get("status", "").strip()
    )
    date_filter = (
        request.GET.get("date", "").strip() or request.POST.get("date", "").strip()
    )
    page = request.GET.get("page", 1) or request.POST.get("page", 1)
    per_page = parse_per_page(request, default=50)

    data = get_workbench_pipeline_items(
        search_query=search_query,
        status_filter=status_filter,
        date_filter=date_filter,
        page=page,
        per_page=per_page,
    )

    context = {
        "active": "tasks",
        "per_page": per_page,
        **data,
    }
    if extra_context:
        context.update(extra_context)
    return render(request, "ui/partials/workbench_table.html", context)


@require_GET
def tasks_workbench_view(request):
    """Notes Generation Workbench: monitor pipeline runs, resolve diffs, review notes."""
    if request.headers.get("HX-Request"):
        return _render_workbench_table(request)

    search_query = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()
    date_filter = request.GET.get("date", "").strip()
    page = request.GET.get("page", 1)
    per_page = parse_per_page(request, default=50)

    data = get_workbench_pipeline_items(
        search_query=search_query,
        status_filter=status_filter,
        date_filter=date_filter,
        page=page,
        per_page=per_page,
    )

    context = {
        "active": "tasks",
        "per_page": per_page,
        **data,
    }
    return render(request, "ui/tasks_workbench.html", context)


# Route aliases
tasks_poll_view = tasks_workbench_view
tasks_list_view = tasks_workbench_view


@require_http_methods(["GET", "POST"])
def generate_settings_modal_view(request):
    """Render the Settings Override modal before launching or regenerating notes."""
    raw_ids = request.POST.getlist("problem_ids") or request.GET.getlist("problem_ids")
    problem_ids = []
    for raw in raw_ids:
        try:
            problem_ids.append(int(raw))
        except (ValueError, TypeError):
            continue

    cfg = load_config()
    default_prov = default_provider_name()
    providers = provider_names()

    default_mod = ""
    if default_prov == "agy":
        default_mod = cfg.agy.default_model
    elif default_prov in cfg.providers:
        default_mod = cfg.providers[default_prov].default_model

    context = {
        "problem_ids": problem_ids,
        "problem_count": len(problem_ids),
        "providers": providers,
        "default_provider": default_prov,
        "default_model": default_mod,
        "review_default": cfg.review_before_writing,
    }
    return render(request, "ui/partials/modals/modal_generate_settings.html", context)


@require_POST
def tasks_create_view(request):
    """Start or reset pipeline generation for selected problems with modal settings overrides."""
    raw_ids = request.POST.getlist("problem_ids")
    problem_ids = []
    for raw in raw_ids:
        try:
            problem_ids.append(int(raw))
        except (ValueError, TypeError):
            continue

    if problem_ids:
        problems = Problem.objects.filter(pk__in=problem_ids)
        if problems.exists():
            overrides = {}
            if "provider" in request.POST and request.POST.get("provider").strip():
                overrides["provider"] = request.POST.get("provider").strip()
            if "model" in request.POST and request.POST.get("model").strip():
                overrides["model"] = request.POST.get("model").strip()
            if "review" in request.POST:
                overrides["review"] = request.POST.get("review") == "true"
            if "regenerate_prefill" in request.POST:
                overrides["regenerate_prefill"] = (
                    request.POST.get("regenerate_prefill") == "true"
                )
            if "refetch" in request.POST:
                overrides["refetch"] = request.POST.get("refetch") == "true"

            try:
                start_pipeline(problems, overrides=overrides, allow_busy=True)
            except Exception as e:
                logger.exception("start_pipeline_failed", error=str(e))

    if request.headers.get("HX-Request"):
        current_url = request.headers.get("HX-Current-URL", "")
        if "new" in current_url:
            response = HttpResponse()
            response["HX-Redirect"] = reverse("ui:tasks")
            return response
        table_html = _render_workbench_table(request).content.decode("utf-8")
        oob_clear = '<div id="modal-container" hx-swap-oob="innerHTML"></div>'
        return HttpResponse(table_html + oob_clear)

    return redirect("ui:tasks")


tasks_regenerate_view = tasks_create_view


@require_POST
def resolve_submission_choice_view(request, item_id: int):
    """Resolve submission conflict: keep existing DB code or update to latest LeetCode submission."""
    choice = request.POST.get("choice", "keep").strip()
    try:
        resolve_submission_choice(item_id, choice)
    except Exception as e:
        logger.exception(
            "resolve_submission_choice_failed", item_id=item_id, error=str(e)
        )

    table_html = _render_workbench_table(request).content.decode("utf-8")
    oob_clear = '<div id="modal-container" hx-swap-oob="innerHTML"></div>'
    return HttpResponse(table_html + oob_clear)


@require_POST
def switch_provider_prefill_view(request, item_id: int):
    """Switch AI provider and settings in-place for prefill stage and re-queue."""
    provider = request.POST.get("provider", "claude").strip()
    model = request.POST.get("model", "").strip()
    effort = request.POST.get("effort", "").strip()

    try:
        switch_provider_and_generate(item_id, provider, model, effort)
    except Exception as e:
        logger.exception(
            "switch_provider_prefill_failed", item_id=item_id, error=str(e)
        )

    table_html = _render_workbench_table(request).content.decode("utf-8")
    oob_clear = '<div id="modal-container" hx-swap-oob="innerHTML"></div>'
    return HttpResponse(table_html + oob_clear)


@require_POST
def select_prefill_response_view(request, item_id: int, response_id: int):
    """Select an existing AIResponse candidate and advance the pipeline."""
    try:
        select_existing_prefill(item_id, response_id)
    except Exception as e:
        logger.exception(
            "select_prefill_response_failed", item_id=item_id, error=str(e)
        )

    table_html = _render_workbench_table(request).content.decode("utf-8")
    oob_clear = '<div id="modal-container" hx-swap-oob="innerHTML"></div>'
    return HttpResponse(table_html + oob_clear)


@require_GET
def preview_ai_response_view(request, item_id: int, response_id: int):
    """Return rendered HTML markdown preview for a selected AIResponse candidate."""
    try:
        item = PipelineItem.objects.select_related("problem").get(pk=item_id)
        resp = AIResponse.objects.get(pk=response_id)
        html = render_ai_response_to_html(item.problem, resp)
    except Exception as e:
        html = (
            f"<div class='p-4 text-xs text-rose-500'>Error loading preview: {e}</div>"
        )
    return HttpResponse(html)


@require_http_methods(["DELETE", "POST"])
def delete_ai_response_view(request, item_id: int, response_id: int):
    """Delete an AIResponse candidate and return the updated candidate cards partial."""
    try:
        item = PipelineItem.objects.select_related("problem").get(pk=item_id)
        delete_ai_response(item_id, response_id)
        responses = list(
            AIResponse.objects.filter(problem=item.problem).order_by("-created_at")
        )
        selected_response = next((r for r in responses if r.is_active), None) or (
            responses[0] if responses else None
        )
        return render(
            request,
            "ui/partials/modals/prefill_candidate_list.html",
            {
                "item": item,
                "responses": responses,
                "selected_response": selected_response,
            },
        )
    except Exception as e:
        logger.exception("delete_ai_response_failed", error=str(e))
        return HttpResponse(
            f"<div class='p-2 text-xs text-rose-500'>Error deleting candidate: {e}</div>"
        )


@require_GET
def stage_modal_content_view(request, item_id: int, stage: str):
    """Render the modal for a waiting or failed stage."""
    try:
        item = PipelineItem.objects.select_related("problem").get(pk=item_id)
    except PipelineItem.DoesNotExist:
        raise Http404("PipelineItem not found.")

    context = get_modal_data_for_item_stage(item, stage)

    if stage == Stage.FETCH_SUBMISSION:
        tpl = "ui/partials/modals/modal_stale_submission.html"
    elif stage == Stage.GENERATE_PREFILL:
        tpl = "ui/partials/modals/modal_prefill_studio.html"
    elif stage == Stage.REVIEW:
        tpl = "ui/partials/modals/modal_review.html"
    else:
        tpl = "ui/partials/modals/modal_stage_error.html"

    return render(request, tpl, context)


@require_POST
def submit_prefill_view(request, item_id: int):
    """Submit pasted manual AI response, advance pipeline, and close modal in-place."""
    text = request.POST.get("response_text", "").strip()
    label = request.POST.get("label", "").strip()

    try:
        submit_manual_reply(item_id, text, label=label)
    except Exception as e:
        logger.exception("submit_prefill_failed", item_id=item_id, error=str(e))

    table_html = _render_workbench_table(request).content.decode("utf-8")
    oob_clear = '<div id="modal-container" hx-swap-oob="innerHTML"></div>'
    return HttpResponse(table_html + oob_clear)


@require_POST
def approve_review_view(request, item_id: int):
    """Approve a note waiting for review, proceed to save notes, and close modal in-place."""
    try:
        approve_review(item_id)
    except Exception as e:
        logger.exception("approve_review_failed", item_id=item_id, error=str(e))

    table_html = _render_workbench_table(request).content.decode("utf-8")
    oob_clear = '<div id="modal-container" hx-swap-oob="innerHTML"></div>'
    return HttpResponse(table_html + oob_clear)


@require_POST
def reject_review_view(request, item_id: int):
    """Reject a note waiting for review, reset to prefill stage with manual tab, and close modal."""
    try:
        reject_review(item_id)
    except Exception as e:
        logger.exception("reject_review_failed", item_id=item_id, error=str(e))

    table_html = _render_workbench_table(request).content.decode("utf-8")
    oob_clear = '<div id="modal-container" hx-swap-oob="innerHTML"></div>'
    return HttpResponse(table_html + oob_clear)


@require_POST
def approve_all_reviews_view(request, task_id: Any = None):
    """Bulk-approve all notes waiting for review."""
    try:
        approve_all_reviews()
    except Exception as e:
        logger.exception("approve_all_reviews_failed", error=str(e))

    return _render_workbench_table(request)


@require_POST
def retry_item_view(request, item_id: int):
    """Retry a failed stage of a PipelineItem and close modal in-place."""
    try:
        retry_failed_item(item_id)
    except Exception as e:
        logger.exception("retry_item_failed", item_id=item_id, error=str(e))

    table_html = _render_workbench_table(request).content.decode("utf-8")
    oob_clear = '<div id="modal-container" hx-swap-oob="innerHTML"></div>'
    return HttpResponse(table_html + oob_clear)


@require_POST
def pause_item_view(request, item_id: int):
    """Pause an active pipeline item."""
    try:
        pause_item(item_id)
    except Exception as e:
        logger.exception("pause_item_failed", item_id=item_id, error=str(e))
    return _render_workbench_table(request)


@require_POST
def resume_item_view(request, item_id: int):
    """Resume a paused pipeline item."""
    try:
        resume_item(item_id)
    except Exception as e:
        logger.exception("resume_item_failed", item_id=item_id, error=str(e))
    return _render_workbench_table(request)


@require_POST
def cancel_item_view(request, item_id: int):
    """Cancel an active pipeline item."""
    try:
        cancel_item(item_id)
    except Exception as e:
        logger.exception("cancel_item_failed", item_id=item_id, error=str(e))

    return _render_workbench_table(request)
