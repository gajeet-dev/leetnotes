from datetime import datetime
from pathlib import Path

from django.utils import timezone
from jinja2 import Environment, FileSystemLoader, StrictUndefined

TEMPLATE_DIR = Path(__file__).parent / "note_templates"

# LeetCode language names that code fences / Obsidian highlighting don't know.
_LANG_ALIASES = {
    "python3": "python",
    "pythondata": "python",
    "mysql": "sql",
    "mssql": "sql",
    "oraclesql": "sql",
    "postgresql": "sql",
    "golang": "go",
}


def code_lang(lang: str) -> str:
    key = lang.lower()
    return _LANG_ALIASES.get(key, key)


def local_dt(value: datetime | None, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """Format an aware datetime in the configured TIME_ZONE."""
    if value is None:
        return ""
    return timezone.localtime(value).strftime(fmt)


_env = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    autoescape=False,  # markdown, not HTML
    trim_blocks=True,  # no blank line after {% ... %} lines
    lstrip_blocks=True,  # no indentation before {% ... %} lines
    keep_trailing_newline=True,
    undefined=StrictUndefined,  # a typo'd variable fails loudly
)
_env.filters["code_lang"] = code_lang
_env.filters["localtime"] = local_dt


def render_template(name: str, context: dict) -> str:
    return _env.get_template(name).render(**context)
