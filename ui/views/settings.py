import json

from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST, require_GET

from prefill.providers.discovery import get_agy_models
from userconfig import AppConfig, load_config, save_config

from ..helpers.settings import (
    auto_set_timezone,
    get_settings_context,
    save_general_settings,
    save_leetcode_credentials,
    test_leetcode_connection,
)


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
        preserved = details.get("preserved_previous", False)
        if ok:
            toast_status = "success"
        elif preserved:
            toast_status = "warning"
        else:
            toast_status = "error"

        # Stay until closed for preserved credentials warning or error
        is_persistent = bool(preserved or not ok)

        toast_html = render(
            request,
            "ui/partials/toast.html",
            {
                "status": toast_status,
                "message": msg,
                "persistent": is_persistent,
            },
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
        response = HttpResponse(
            toast_html
            + f'<div id="connection-status-panel" hx-swap-oob="outerHTML">{status_panel_html}</div>'
        )
        if preserved:
            response["HX-Trigger"] = json.dumps({"clearCredentialsInputs": True})
        return response

    return JsonResponse({"ok": ok, "message": msg, "details": details})


@require_http_methods(["GET", "POST"])
def test_connection_view(request):
    """Check connectivity to LeetCode with the currently saved cookies."""
    force = request.GET.get("force") in ("true", "1") or request.POST.get("force") in (
        "true",
        "1",
    )
    res = test_leetcode_connection(force=force)
    source = request.GET.get("source") or request.POST.get("source")
    if source == "dashboard":
        return render(
            request,
            "ui/partials/dashboard_connection_status.html",
            {"conn": res},
        )
    if request.headers.get("HX-Request"):
        return render(request, "ui/partials/connection_status.html", {"conn": res})
    return JsonResponse(res)


@require_POST
def save_general_settings_view(request):
    """Save notes output directory, timezone, sync pacing, and AI preferences."""
    output_dir = request.POST.get("output_dir")
    create_directory = (
        request.POST.get("create_directory") in ("true", "on", "1")
        if ("output_dir" in request.POST or "create_directory" in request.POST)
        else None
    )
    time_zone = request.POST.get("time_zone")

    raw_ppm = request.POST.get("problems_per_minute")
    try:
        ppm = float(raw_ppm) if raw_ppm else None
    except (ValueError, TypeError):
        ppm = None

    provider = request.POST.get("default_provider")
    review = (
        request.POST.get("review_before_writing") in ("true", "on", "1")
        if (
            "default_provider" in request.POST
            or "problems_per_minute" in request.POST
            or "review_before_writing" in request.POST
        )
        else None
    )

    # Claude Code settings
    claude_model_choice = request.POST.get("claude_model_choice")
    if claude_model_choice == "custom":
        claude_model = request.POST.get("claude_model_custom", "").strip() or "sonnet"
    elif claude_model_choice is not None:
        claude_model = claude_model_choice.strip()
    else:
        claude_model = None
    claude_effort = request.POST.get("claude_effort")

    # Antigravity CLI settings
    agy_model_choice = request.POST.get("agy_model_choice")
    if agy_model_choice == "custom":
        agy_model = request.POST.get("agy_model_custom", "").strip()
    elif agy_model_choice is not None:
        agy_model = agy_model_choice.strip()
    else:
        agy_model = None
    agy_effort = request.POST.get("agy_effort")

    ok, msg, details = save_general_settings(
        output_dir=output_dir,
        create_directory=create_directory,
        time_zone=time_zone,
        problems_per_minute=ppm,
        default_provider=provider,
        review_before_writing=review,
        claude_model=claude_model,
        claude_effort=claude_effort,
        agy_model=agy_model,
        agy_effort=agy_effort,
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
        return JsonResponse(
            {"status": "error", "message": "Missing timezone"}, status=400
        )

    ok, msg = auto_set_timezone(tz_name)
    return JsonResponse({"status": "ok" if ok else "skipped", "message": msg})


@require_POST
def refresh_agy_models_view(request):
    """Fetch the latest available models from Antigravity CLI and update the models dropdown."""
    models = get_agy_models(force_refresh=True)
    config = load_config()
    saved_agy = (config.providers or {}).get("agy", {})
    current_model = saved_agy.get("model", "")
    known_ids = {m["id"] for m in models}
    is_custom = bool(current_model and current_model not in known_ids)

    if models:
        status = "success"
        msg = f"Retrieved {len(models)} models from Antigravity CLI."
    else:
        status = "warning"
        msg = "Could not retrieve models from Antigravity CLI. You can enter a custom model name."

    if request.headers.get("HX-Request"):
        toast_html = render(
            request,
            "ui/partials/toast.html",
            {"status": status, "message": msg},
        ).content.decode("utf-8")

        dropdown_html = render(
            request,
            "ui/partials/agy_model_dropdown.html",
            {
                "agy_models": models,
                "agy_model": current_model,
                "agy_is_custom": is_custom,
                "oob": True,
            },
        ).content.decode("utf-8")

        return HttpResponse(toast_html + dropdown_html)

    return redirect("ui:settings")


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
