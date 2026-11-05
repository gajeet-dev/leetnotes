"""Server-side markdown rendering helper for solution note review."""

from __future__ import annotations

import re
from typing import Any

import markdown


def render_markdown_note(raw_markdown: str) -> tuple[dict[str, Any], str]:
    """Parses YAML frontmatter and renders markdown into clean HTML.

    Returns a tuple of (frontmatter_dict, rendered_html).
    """
    if not raw_markdown:
        return {}, ""

    frontmatter: dict[str, Any] = {
        "id": None,
        "slug": None,
        "difficulty": None,
        "tags": [],
        "problem_link": None,
    }
    body = raw_markdown

    if raw_markdown.startswith("---"):
        parts = raw_markdown.split("---", 2)
        if len(parts) >= 3:
            fm_text = parts[1]
            body = parts[2].strip()
            in_tags = False
            tags: list[str] = []
            for line in fm_text.splitlines():
                s = line.strip()
                if s.startswith("tags:"):
                    in_tags = True
                    continue
                if in_tags:
                    if s.startswith("- "):
                        tags.append(s[2:].strip().strip("\"'"))
                    elif ":" in s:
                        in_tags = False
                if s.startswith("id:"):
                    frontmatter["id"] = s.split(":", 1)[1].strip()
                elif s.startswith("slug:"):
                    frontmatter["slug"] = s.split(":", 1)[1].strip()
                elif s.startswith("difficulty:"):
                    frontmatter["difficulty"] = s.split(":", 1)[1].strip()
                elif s.startswith("problem_link:"):
                    frontmatter["problem_link"] = (
                        s.split(":", 1)[1].strip().strip("\"'")
                    )
            frontmatter["tags"] = tags

    # Convert Obsidian wikilinks [[slug|label]] -> span
    body = re.sub(
        r"\[\[([^\|\]]+)\|([^\]]+)\]\]",
        r'<span class="font-mono font-medium text-emerald-600 dark:text-emerald-400">\2</span>',
        body,
    )
    body = re.sub(
        r"\[\[([^\]]+)\]\]",
        r'<span class="font-mono font-medium text-emerald-600 dark:text-emerald-400">\1</span>',
        body,
    )

    rendered_html = markdown.markdown(
        body,
        extensions=["fenced_code", "tables", "nl2br"],
    )

    return frontmatter, rendered_html
