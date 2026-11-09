"""Discovery and caching of available CLI models and presets."""

import json
import re
import subprocess
from datetime import UTC, datetime

import structlog

from userconfig.paths import agy_models_cache_path

logger = structlog.get_logger(__name__)

CLAUDE_PRESET_MODELS: list[dict[str, str]] = [
    {"id": "sonnet", "name": "Claude Sonnet (Recommended)"},
    {"id": "opus", "name": "Claude Opus"},
    {"id": "haiku", "name": "Claude Haiku"},
]

EFFORT_LEVELS: list[dict[str, str]] = [
    {"id": "", "name": "Default (CLI default)"},
    {"id": "low", "name": "Low"},
    {"id": "medium", "name": "Medium"},
    {"id": "high", "name": "High"},
    {"id": "xhigh", "name": "Extra High"},
    {"id": "max", "name": "Max"},
]


def parse_agy_models_output(output: str) -> list[dict[str, str]]:
    """Parse output of `agy models` into a list of model dicts with id and display name."""
    models: list[dict[str, str]] = []
    for line in output.splitlines():
        clean = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", line).strip()
        if not clean or "Fetching" in clean:
            continue
        if "\t" in clean:
            parts = clean.split("\t", 1)
        else:
            parts = re.split(r"\s{2,}", clean, maxsplit=1)

        if len(parts) == 2:
            model_id = parts[0].strip()
            display_name = parts[1].strip()
            if model_id and display_name:
                models.append({"id": model_id, "name": display_name})
        elif len(parts) == 1 and parts[0].strip():
            model_id = parts[0].strip()
            models.append({"id": model_id, "name": model_id})

    return models


def fetch_agy_models(timeout: float = 8.0) -> list[dict[str, str]]:
    """Run `agy models` CLI command and return parsed models."""
    try:
        proc = subprocess.run(
            ["agy", "models"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode == 0 and proc.stdout:
            return parse_agy_models_output(proc.stdout)
        logger.warning(
            "agy_models_fetch_failed",
            returncode=proc.returncode,
            stderr=proc.stderr[:300] if proc.stderr else "",
        )
    except FileNotFoundError:
        logger.info("agy_cli_not_found")
    except subprocess.TimeoutExpired:
        logger.warning("agy_models_fetch_timed_out", timeout=timeout)
    except Exception as exc:
        logger.warning("agy_models_fetch_error", error=str(exc))
    return []


def save_cached_agy_models(models: list[dict[str, str]]) -> None:
    """Save models list to data/agy_models.json."""
    cache_file = agy_models_cache_path()
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        data = {
            "updated_at": datetime.now(UTC).isoformat(),
            "models": models,
        }
        temp_file = cache_file.with_suffix(".tmp")
        temp_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temp_file.replace(cache_file)
    except OSError as exc:
        logger.warning("agy_models_cache_save_failed", error=str(exc))


def load_cached_agy_models() -> list[dict[str, str]] | None:
    """Load models from data/agy_models.json if it exists."""
    cache_file = agy_models_cache_path()
    if not cache_file.is_file():
        return None
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        models = data.get("models")
        if isinstance(models, list):
            return models
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("agy_models_cache_read_failed", error=str(exc))
    return None


def get_agy_models(force_refresh: bool = False) -> list[dict[str, str]]:
    """Return available Antigravity models, using disk cache unless force_refresh is True."""
    if not force_refresh:
        cached = load_cached_agy_models()
        if cached is not None:
            return cached

    models = fetch_agy_models()
    if models:
        save_cached_agy_models(models)
        return models

    # Fallback to previously cached models if fresh fetch returned nothing
    cached = load_cached_agy_models()
    return cached or []
