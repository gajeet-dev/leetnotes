from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core.cache import cache

from leetcode.bootstrap import ClientSettings, build_client, reset_client
from leetcode.connection import ConnectionResult, check_connection
from notes.models import NoteRecord
from prefill.models import AIResponse
from prefill.providers.config import (
    all_providers,
    default_provider_name,
    provider_names,
)
from prefill.providers.discovery import (
    CLAUDE_PRESET_MODELS,
    EFFORT_LEVELS,
    get_agy_models,
)
from problems.models import Problem, Submission
from problems.services.snapshots import list_snapshots
from userconfig import effective
from userconfig.credentials import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    LeetCodeCredentials,
    clean_cookie_value,
    load_credentials,
    mask_secret,
    save_credentials,
    update_credentials,
)
from userconfig.errors import ConfigError
from userconfig.store import load_config, update_config

CONNECTION_CACHE_KEY = "leetcode_connection_status"
CONNECTION_COOLDOWN_SECONDS = 15


def clear_leetcode_connection_cache() -> None:
    """Clear the cached connection result so the next check queries LeetCode live."""
    cache.delete(CONNECTION_CACHE_KEY)


def check_tokens_connection(session: str, csrf_token: str) -> ConnectionResult:
    """Test connectivity for an explicit pair of session and csrf tokens."""
    config = ClientSettings(
        session=session,
        csrf_token=csrf_token,
        base_url=settings.LEETCODE_BASE_URL,
        requests_per_second=effective.requests_per_second(),
        requests_per_minute=effective.requests_per_minute(),
        username=effective.leetcode_username(),
    )
    client = build_client(config)
    return check_connection(client_factory=lambda: client)


def get_settings_context() -> dict:
    """Gather current user configuration, credentials, and connection state for the Settings page."""
    config = load_config()
    creds = load_credentials()

    raw_session = effective.leetcode_session()
    raw_csrf = effective.leetcode_csrf_token()

    dirs = effective.output_dirs()
    output_dir_str = str(dirs[0]) if dirs else ""
    output_dir_exists = (
        Path(output_dir_str).expanduser().is_dir() if output_dir_str else False
    )

    current_tz = config.time_zone or "UTC"

    try:
        tz_obj = ZoneInfo(current_tz)
        local_time_display = datetime.now(tz_obj).strftime("%I:%M %p (%a, %b %d)")
    except ZoneInfoNotFoundError, ValueError, TypeError, KeyError:
        local_time_display = ""

    common_timezones = [
        "UTC",
        "Asia/Kolkata",
        "America/New_York",
        "America/Chicago",
        "America/Denver",
        "America/Los_Angeles",
        "Europe/London",
        "Europe/Paris",
        "Europe/Berlin",
        "Asia/Tokyo",
        "Asia/Shanghai",
        "Asia/Singapore",
        "Australia/Sydney",
    ]
    if current_tz and current_tz not in common_timezones:
        common_timezones.append(current_tz)

    effective_ppm = effective.problems_per_minute()
    ppm_display = int(effective_ppm) if effective_ppm.is_integer() else effective_ppm

    saved_providers = config.providers or {}
    claude_saved = saved_providers.get("claude", {})
    agy_saved = saved_providers.get("agy", {})

    claude_model = claude_saved.get("model", "sonnet")
    claude_default_effort = all_providers().get("claude", {}).get("effort", "medium")
    claude_effort = (
        claude_saved.get("effort")
        if "effort" in claude_saved
        else claude_default_effort
    )
    claude_preset_ids = {m["id"] for m in CLAUDE_PRESET_MODELS}
    claude_is_custom = bool(claude_model and claude_model not in claude_preset_ids)

    agy_model = agy_saved.get("model", "")
    agy_default_effort = all_providers().get("agy", {}).get("effort", "medium")
    agy_effort = (
        agy_saved.get("effort") if "effort" in agy_saved else agy_default_effort
    )
    agy_models = get_agy_models()
    agy_known_ids = {m["id"] for m in agy_models}
    agy_is_custom = bool(agy_model and agy_model not in agy_known_ids)

    # Fetch total record counts for problems, user submissions, AI responses, and note tracking records
    total_problems_count = Problem.objects.count()
    total_submissions_count = Submission.objects.count()
    total_ai_responses_count = AIResponse.objects.count()
    total_note_records_count = NoteRecord.objects.count()

    database_counts = {
        "problems": total_problems_count,
        "submissions": total_submissions_count,
        "ai_responses": total_ai_responses_count,
        "note_records": total_note_records_count,
    }

    return {
        "active": "settings",
        "session_hint": mask_secret(raw_session),
        "csrf_hint": mask_secret(raw_csrf),
        "credentials_configured": bool(raw_session and raw_csrf),
        "credentials_stored_in_secrets": creds.is_complete,
        "credentials_updated_at": creds.updated_at,
        "leetcode_username": effective.leetcode_username(),
        "output_dir": output_dir_str,
        "output_dir_exists": output_dir_exists,
        "create_directory": config.create_directory,
        "time_zone": current_tz,
        "is_time_zone_saved": config.time_zone is not None,
        "local_time_display": local_time_display,
        "common_timezones": common_timezones,
        "problems_per_minute": ppm_display,
        "review_before_writing": config.review_before_writing,
        "default_provider": default_provider_name(),
        "available_providers": provider_names(),
        "claude_model": claude_model,
        "claude_effort": claude_effort,
        "claude_is_custom": claude_is_custom,
        "claude_preset_models": CLAUDE_PRESET_MODELS,
        "claude_effort_levels": EFFORT_LEVELS,
        "agy_model": agy_model,
        "agy_effort": agy_effort,
        "agy_is_custom": agy_is_custom,
        "agy_models": agy_models,
        "agy_effort_levels": EFFORT_LEVELS,
        "available_snapshots": list_snapshots(),
        "db_counts": database_counts,
    }


def save_leetcode_credentials(
    session: str | None = None, csrf_token: str | None = None
) -> tuple[bool, str, dict]:
    """Validate and store LeetCode cookies, then test connection and auto-set username.

    If editing existing working credentials results in a connection failure, the previous
    working credentials are preserved intact. If the existing credentials were already expired
    or invalid, the new credentials are saved even if connection fails.
    """
    old_creds = load_credentials()
    old_session = old_creds.session
    old_csrf = old_creds.csrf_token
    has_old = bool(old_session and old_csrf)

    # Clean candidate cookie values
    try:
        candidate_session = (
            clean_cookie_value(session, SESSION_COOKIE)
            if (session and session.strip())
            else old_session
        )
    except ValueError as exc:
        if has_old:
            old_conn = check_tokens_connection(old_session, old_csrf)
            if old_conn.ok:
                known_username = (
                    old_conn.username
                    or effective.leetcode_username()
                    or load_config().leetcode_username
                    or ""
                )
                user_suffix = f" for {known_username}" if known_username else ""
                return (
                    False,
                    f"Invalid session cookie format: {exc}. Your previous working credentials{user_suffix} were kept.",
                    {
                        "ok": True,
                        "state": old_conn.state.value,
                        "username": known_username,
                        "is_premium": old_conn.is_premium,
                        "preserved_previous": True,
                        "rejected_error": str(exc),
                    },
                )
        return (
            False,
            f"Invalid session cookie: {exc}",
            {"ok": False, "state": "invalid", "message": str(exc)},
        )

    try:
        candidate_csrf = (
            clean_cookie_value(csrf_token, CSRF_COOKIE)
            if (csrf_token and csrf_token.strip())
            else old_csrf
        )
    except ValueError as exc:
        if has_old:
            old_conn = check_tokens_connection(old_session, old_csrf)
            if old_conn.ok:
                known_username = (
                    old_conn.username
                    or effective.leetcode_username()
                    or load_config().leetcode_username
                    or ""
                )
                user_suffix = f" for {known_username}" if known_username else ""
                return (
                    False,
                    f"Invalid CSRF token format: {exc}. Your previous working credentials{user_suffix} were kept.",
                    {
                        "ok": True,
                        "state": old_conn.state.value,
                        "username": known_username,
                        "is_premium": old_conn.is_premium,
                        "preserved_previous": True,
                        "rejected_error": str(exc),
                    },
                )
        return (
            False,
            f"Invalid CSRF token: {exc}",
            {"ok": False, "state": "invalid", "message": str(exc)},
        )

    if not (candidate_session and candidate_csrf):
        return (
            False,
            "Please provide both session cookie and CSRF token.",
            {
                "ok": False,
                "state": "not_configured",
                "message": "Incomplete credentials",
            },
        )

    # Test candidate credentials with LeetCode
    candidate_conn = check_tokens_connection(candidate_session, candidate_csrf)

    if candidate_conn.ok:
        # Candidate credentials work! Save them to secrets.json
        new_creds = LeetCodeCredentials(
            session=candidate_session,
            csrf_token=candidate_csrf,
            updated_at=datetime.now(timezone.utc),
        )
        save_credentials(new_creds)
        reset_client()
        clear_leetcode_connection_cache()
        if candidate_conn.username:
            update_config({"leetcode_username": candidate_conn.username})

        cand_real_name = (
            candidate_conn.real_name
            if isinstance(getattr(candidate_conn, "real_name", None), str)
            else ""
        )
        cand_display_name = (
            candidate_conn.display_name
            if isinstance(getattr(candidate_conn, "display_name", None), str)
            else (
                candidate_conn.username
                if isinstance(getattr(candidate_conn, "username", None), str)
                else ""
            )
        )
        details = {
            "ok": True,
            "state": (
                candidate_conn.state.value
                if hasattr(candidate_conn.state, "value")
                else str(candidate_conn.state)
            ),
            "username": (
                candidate_conn.username
                if isinstance(getattr(candidate_conn, "username", None), str)
                else ""
            ),
            "real_name": cand_real_name,
            "display_name": cand_display_name,
            "is_premium": bool(getattr(candidate_conn, "is_premium", False)),
        }
        return (
            True,
            f"Credentials saved and verified! Connected as {cand_display_name}.",
            details,
        )

    # Candidate credentials failed! Check if previous credentials were valid
    tokens_changed = (candidate_session != old_session) or (candidate_csrf != old_csrf)
    if tokens_changed and has_old:
        old_conn = check_tokens_connection(old_session, old_csrf)
        if old_conn.ok:
            # Previous credentials were valid and working! Keep them and reject the broken ones.
            reset_client()
            known_username = (
                old_conn.username
                or effective.leetcode_username()
                or load_config().leetcode_username
                or ""
            )
            user_suffix = f" for {known_username}" if known_username else ""
            err_detail = (candidate_conn.message or "cookies were rejected").rstrip(".")
            old_real_name = (
                old_conn.real_name
                if isinstance(getattr(old_conn, "real_name", None), str)
                else ""
            )
            old_display_name = (
                old_conn.display_name
                if isinstance(getattr(old_conn, "display_name", None), str)
                else (known_username if isinstance(known_username, str) else "")
            )
            details = {
                "ok": True,
                "state": (
                    old_conn.state.value
                    if hasattr(old_conn.state, "value")
                    else str(old_conn.state)
                ),
                "username": known_username,
                "real_name": old_real_name,
                "display_name": old_display_name,
                "is_premium": bool(getattr(old_conn, "is_premium", False)),
                "preserved_previous": True,
                "rejected_error": candidate_conn.message,
            }
            msg = (
                f"New cookies failed connection: {err_detail}. "
                f"Your previous working credentials{user_suffix} were kept."
            )
            return False, msg, details

    # If old credentials were also invalid (or didn't exist, or same credentials), save candidate
    new_creds = LeetCodeCredentials(
        session=candidate_session,
        csrf_token=candidate_csrf,
        updated_at=datetime.now(timezone.utc),
    )
    save_credentials(new_creds)
    reset_client()
    clear_leetcode_connection_cache()

    details = {
        "ok": False,
        "state": (
            candidate_conn.state.value
            if hasattr(candidate_conn.state, "value")
            else str(candidate_conn.state)
        ),
        "message": candidate_conn.message,
        "username": "",
        "real_name": "",
        "display_name": "",
        "is_premium": False,
    }
    if candidate_conn.state.value == "signed_out":
        msg = f"Cookies saved, but LeetCode reported signed out. Please verify your tokens."
    else:
        msg = f"Cookies saved, but connection check failed: {candidate_conn.message}"
    return False, msg, details


def test_leetcode_connection(force: bool = False) -> dict:
    """Check connectivity to LeetCode with the currently saved cookies.

    Debounced server-side: returns cached result if verified successfully within
    CONNECTION_COOLDOWN_SECONDS, unless force=True.
    Only successful connections are cached; failures/expired cookies are never cached
    so that subsequent attempts always verify live against LeetCode.
    """
    if not force:
        cached = cache.get(CONNECTION_CACHE_KEY)
        if cached is not None and cached.get("ok"):
            return cached

    reset_client()
    conn = check_connection()

    if conn.ok and conn.username:
        # Keep username up to date in config
        update_config({"leetcode_username": conn.username})

    real_name = (
        conn.real_name if isinstance(getattr(conn, "real_name", None), str) else ""
    )
    display_name = (
        conn.display_name
        if isinstance(getattr(conn, "display_name", None), str)
        else (conn.username if isinstance(getattr(conn, "username", None), str) else "")
    )

    result = {
        "ok": conn.ok,
        "state": conn.state.value if hasattr(conn.state, "value") else str(conn.state),
        "message": conn.message,
        "username": conn.username
        if isinstance(getattr(conn, "username", None), str)
        else "",
        "real_name": real_name,
        "display_name": display_name,
        "is_premium": bool(getattr(conn, "is_premium", False)),
        "cached": False,
    }

    if conn.ok:
        cache.set(
            CONNECTION_CACHE_KEY,
            {**result, "cached": True},
            timeout=CONNECTION_COOLDOWN_SECONDS,
        )
    else:
        # Never cache failures or expired sessions: ensure cache is purged so next check is live
        cache.delete(CONNECTION_CACHE_KEY)

    return result


def save_general_settings(
    *,
    output_dir: str | None = None,
    create_directory: bool | None = None,
    time_zone: str | None = None,
    problems_per_minute: float | None = None,
    default_provider: str | None = None,
    review_before_writing: bool | None = None,
    claude_model: str | None = None,
    claude_effort: str | None = None,
    agy_model: str | None = None,
    agy_effort: str | None = None,
) -> tuple[bool, str, dict]:
    """Validate and persist notes storage, timezone, sync pacing, and directory creation preferences."""
    changes: dict = {}
    config = load_config()
    if create_directory is not None:
        changes["create_directory"] = bool(create_directory)

    target_path = None
    cleaned_dir = None
    dir_exists = False
    dir_created = False

    if output_dir is not None:
        cleaned_dir = output_dir.strip()
        changes["output_dirs"] = [cleaned_dir] if cleaned_dir else None
        if cleaned_dir:
            target_path = Path(cleaned_dir).expanduser()
            dir_exists = target_path.is_dir()
            if not dir_exists and create_directory:
                try:
                    target_path.mkdir(parents=True, exist_ok=True)
                    dir_exists = True
                    dir_created = True
                except OSError as exc:
                    return (
                        False,
                        f"Failed to create directory '{cleaned_dir}': {exc}",
                        {
                            "output_dir": cleaned_dir,
                            "output_dir_exists": False,
                            "create_directory": bool(create_directory),
                            "dir_created": False,
                            "status": "error",
                        },
                    )
    else:
        dirs = effective.output_dirs()
        cleaned_dir = str(dirs[0]) if dirs else ""
        target_path = Path(cleaned_dir).expanduser() if cleaned_dir else None
        dir_exists = target_path.is_dir() if target_path else False

    if time_zone is not None:
        tz_str = time_zone.strip()
        if tz_str:
            try:
                ZoneInfo(tz_str)
                changes["time_zone"] = tz_str
            except ZoneInfoNotFoundError, ValueError:
                return (
                    False,
                    f"Unknown timezone '{tz_str}'. Please use a standard IANA timezone name.",
                    {
                        "output_dir": cleaned_dir,
                        "output_dir_exists": dir_exists,
                        "create_directory": bool(create_directory),
                        "dir_created": dir_created,
                        "status": "error",
                    },
                )
        else:
            changes["time_zone"] = "UTC"

    if problems_per_minute is not None:
        if problems_per_minute <= 0:
            return (
                False,
                "Problems per minute must be greater than 0.",
                {
                    "output_dir": cleaned_dir,
                    "output_dir_exists": dir_exists,
                    "create_directory": bool(create_directory),
                    "dir_created": dir_created,
                    "status": "error",
                },
            )
        changes["problems_per_minute"] = problems_per_minute

    if default_provider is not None:
        cleaned_provider = default_provider.strip()
        if cleaned_provider in provider_names():
            changes["default_provider"] = cleaned_provider

    if review_before_writing is not None:
        changes["review_before_writing"] = bool(review_before_writing)

    if any(v is not None for v in (claude_model, claude_effort, agy_model, agy_effort)):
        current_providers = dict(config.providers or {})

        if claude_model is not None or claude_effort is not None:
            claude_entry = dict(current_providers.get("claude", {}))
            if claude_model is not None:
                claude_entry["model"] = claude_model.strip()
            if claude_effort is not None:
                claude_entry["effort"] = claude_effort.strip()
            current_providers["claude"] = claude_entry

        if agy_model is not None or agy_effort is not None:
            agy_entry = dict(current_providers.get("agy", {}))
            if agy_model is not None:
                agy_entry["model"] = agy_model.strip()
            if agy_effort is not None:
                agy_entry["effort"] = agy_effort.strip()
            current_providers["agy"] = agy_entry

        changes["providers"] = current_providers

    try:
        update_config(changes)
    except ConfigError as exc:
        return (
            False,
            str(exc),
            {
                "output_dir": cleaned_dir,
                "output_dir_exists": dir_exists,
                "create_directory": create_directory,
                "dir_created": dir_created,
                "status": "error",
            },
        )

    # Determine message and status based on directory existence
    if target_path and dir_created:
        msg = f"Settings saved. Created directory: {cleaned_dir}"
        status = "success"
    elif target_path and not dir_exists:
        msg = (
            f"Settings saved, but the directory '{cleaned_dir}' does not exist on disk."
        )
        status = "warning"
    else:
        msg = "Settings saved successfully."
        status = "success"

    details = {
        "output_dir": cleaned_dir,
        "output_dir_exists": dir_exists,
        "create_directory": bool(create_directory),
        "dir_created": dir_created,
        "status": status,
    }

    return True, msg, details


def auto_set_timezone(timezone_name: str) -> tuple[bool, str]:
    """Auto-set timezone in config.json if not already configured by user."""
    config = load_config()
    if config.time_zone is not None:
        return False, "Timezone is already set."

    tz_str = timezone_name.strip()
    try:
        ZoneInfo(tz_str)
        update_config({"time_zone": tz_str})
        return True, f"Auto-detected timezone set to {tz_str}."
    except ZoneInfoNotFoundError, ValueError:
        return False, f"Invalid timezone '{tz_str}'."
