from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from leetcode.bootstrap import reset_client
from leetcode.connection import check_connection
from prefill.providers.config import default_provider_name, provider_names
from userconfig import effective
from userconfig.credentials import (
    load_credentials,
    mask_secret,
    update_credentials,
)
from userconfig.errors import ConfigError
from userconfig.store import load_config, update_config


def get_settings_context() -> dict:
    """Gather current user configuration, credentials, and connection state for the Settings page."""
    config = load_config()
    creds = load_credentials()

    raw_session = effective.leetcode_session()
    raw_csrf = effective.leetcode_csrf_token()

    dirs = effective.output_dirs()
    output_dir_str = str(dirs[0]) if dirs else ""
    output_dir_exists = Path(output_dir_str).expanduser().is_dir() if output_dir_str else False

    current_tz = config.time_zone or "UTC"

    try:
        tz_obj = ZoneInfo(current_tz)
        local_time_display = datetime.now(tz_obj).strftime("%I:%M %p (%a, %b %d)")
    except (ZoneInfoNotFoundError, ValueError, TypeError, KeyError):
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
    }


def save_leetcode_credentials(
    session: str | None = None, csrf_token: str | None = None
) -> tuple[bool, str, dict]:
    """Validate and store LeetCode cookies, then test connection and auto-set username."""
    try:
        update_credentials(session=session, csrf_token=csrf_token)
    except ConfigError as exc:
        return False, str(exc), {}

    reset_client()
    conn = check_connection()

    details = {
        "ok": conn.ok,
        "state": conn.state.value,
        "username": conn.username,
        "is_premium": conn.is_premium,
    }

    if conn.ok and conn.username:
        # Automatically set the detected username in config.json
        update_config({"leetcode_username": conn.username})
        return True, f"Credentials saved and verified! Connected as {conn.username}.", details

    if conn.state.value == "signed_out":
        return False, "Cookies saved, but LeetCode reported signed out. Please verify your tokens.", details

    return False, f"Cookies saved, but connection check failed: {conn.message}", details


def test_leetcode_connection() -> dict:
    """Check connectivity to LeetCode with the currently saved cookies."""
    reset_client()
    conn = check_connection()

    if conn.ok and conn.username:
        # Keep username up to date in config
        update_config({"leetcode_username": conn.username})

    return {
        "ok": conn.ok,
        "state": conn.state.value,
        "message": conn.message,
        "username": conn.username,
        "is_premium": conn.is_premium,
    }


def save_general_settings(
    *,
    output_dir: str | None = None,
    create_directory: bool | None = None,
    time_zone: str | None = None,
    problems_per_minute: float | None = None,
    default_provider: str | None = None,
    review_before_writing: bool | None = None,
) -> tuple[bool, str, dict]:
    """Validate and persist notes storage, timezone, sync pacing, and directory creation preferences."""
    changes: dict = {}
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
            except (ZoneInfoNotFoundError, ValueError):
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
        msg = f"Settings saved, but the directory '{cleaned_dir}' does not exist on disk."
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
    except (ZoneInfoNotFoundError, ValueError):
        return False, f"Invalid timezone '{tz_str}'."
