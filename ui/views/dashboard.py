import structlog
from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from leetcode.bootstrap import get_client
from leetcode.exceptions import AuthError, LeetCodeError
from problems.services.sync import sync_leetcode_overview
from userconfig import effective

from ..helpers.dashboard import get_dashboard_metrics
from ..helpers.settings import get_ai_provider_status, get_output_dir_status

logger = structlog.get_logger(__name__)


@require_GET
def dashboard_view(request):
    """Render the dashboard workspace page with overview KPIs and health metrics."""
    ai_status = get_ai_provider_status()
    out_status = get_output_dir_status()
    kpi_metrics = get_dashboard_metrics()
    return render(
        request,
        "ui/dashboard.html",
        {
            "active": "dashboard",
            "ai_status": ai_status,
            "output_dir": out_status["output_dir"],
            "output_dir_exists": out_status["output_dir_exists"],
            "kpi_metrics": kpi_metrics,
        },
    )


@require_POST
def sync_leetcode_data_view(request):
    """Trigger background catalog discovery and recent accepted submissions check."""
    # Check credentials first
    session = effective.leetcode_session()
    csrf_token = effective.leetcode_csrf_token()

    if not (session and csrf_token):
        toast_html = render(
            request,
            "ui/partials/toast.html",
            {
                "status": "warning",
                "message": "LeetCode credentials not configured. Please set them in Settings.",
            },
        ).content.decode("utf-8")
        btn_html = render(
            request, "ui/partials/sync_leetcode_button.html"
        ).content.decode("utf-8")
        return HttpResponse(
            btn_html
            + f'<div id="toast-container" hx-swap-oob="beforeend">{toast_html}</div>'
        )

    # Obtain client using bootstrap get_client()
    try:
        client = get_client()
    except Exception as exc:
        toast_html = render(
            request,
            "ui/partials/toast.html",
            {
                "status": "error",
                "message": f"Unable to initialize LeetCode client: {exc}",
            },
        ).content.decode("utf-8")
        btn_html = render(
            request, "ui/partials/sync_leetcode_button.html"
        ).content.decode("utf-8")
        return HttpResponse(
            btn_html
            + f'<div id="toast-container" hx-swap-oob="beforeend">{toast_html}</div>'
        )

    try:
        sync_result = sync_leetcode_overview(client)
    except (LeetCodeError, AuthError, ValueError) as exc:
        logger.warning("sync_leetcode_data_failed", error=str(exc))
        toast_html = render(
            request,
            "ui/partials/toast.html",
            {
                "status": "error",
                "message": f"Sync failed: {exc}",
            },
        ).content.decode("utf-8")
        btn_html = render(
            request, "ui/partials/sync_leetcode_button.html"
        ).content.decode("utf-8")
        return HttpResponse(
            btn_html
            + f'<div id="toast-container" hx-swap-oob="beforeend">{toast_html}</div>'
        )

    # Calculate updated live metrics for OOB dashboard updates
    kpi_metrics = get_dashboard_metrics()

    btn_html = render(request, "ui/partials/sync_leetcode_button.html").content.decode(
        "utf-8"
    )
    strip_html = render(
        request,
        "ui/partials/sync_stats_strip.html",
        {"sync_stats": sync_result},
    ).content.decode("utf-8")
    solved_kpi_html = render(
        request,
        "ui/partials/dashboard_kpi_solved.html",
        {"kpi_metrics": kpi_metrics},
    ).content.decode("utf-8")
    notes_kpi_html = render(
        request,
        "ui/partials/dashboard_kpi_notes.html",
        {"kpi_metrics": kpi_metrics},
    ).content.decode("utf-8")
    attention_kpi_html = render(
        request,
        "ui/partials/dashboard_kpi_attention.html",
        {"kpi_metrics": kpi_metrics},
    ).content.decode("utf-8")

    response_body = (
        btn_html
        + f'<div id="sync-stats-container" hx-swap-oob="innerHTML">{strip_html}</div>'
        + f'<div id="kpi-solved-problems" hx-swap-oob="outerHTML">{solved_kpi_html}</div>'
        + f'<div id="kpi-notes-created" hx-swap-oob="outerHTML">{notes_kpi_html}</div>'
        + f'<div id="kpi-needs-attention" hx-swap-oob="outerHTML">{attention_kpi_html}</div>'
    )
    return HttpResponse(response_body)
