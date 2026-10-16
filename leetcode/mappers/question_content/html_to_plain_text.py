import html

import structlog
from bs4 import BeautifulSoup

logger = structlog.get_logger(__name__)

BLOCK_TAGS = {
    "p",
    "li",
    "div",
    "pre",
    "tr",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "blockquote",
}


def _preprocess_math_tags(soup: BeautifulSoup) -> None:
    """Formats subscripts and superscripts cleanly."""
    for tag in soup.find_all("sub"):
        tag.insert_before("_")
    for tag in soup.find_all("sup"):
        tag.insert_before("^")


def _format_spacing_and_blocks(soup: BeautifulSoup) -> None:
    """Ensures line breaks are explicitly added to structural elements."""
    for tag in soup.find_all("br"):
        tag.replace_with("\n")

    for tag in soup.find_all(BLOCK_TAGS):
        tag.append("\n")


def _normalize_whitespace(text: str) -> str:
    """Collapses trailing space and suppresses multi-line whitespace runs."""
    lines = [line.rstrip() for line in text.splitlines()]
    cleaned = []
    blank_run = 0

    for line in lines:
        if not line.strip():
            blank_run += 1
            if blank_run <= 1:
                cleaned.append("")
        else:
            blank_run = 0
            cleaned.append(line.strip())

    return "\n".join(cleaned).strip()


def html_to_plain_text(raw_html: str | None) -> str:
    """Main parser pipeline converting LeetCode question HTML into clean plain text."""
    if not raw_html:
        return ""

    try:
        unescaped = html.unescape(raw_html)
        soup = BeautifulSoup(unescaped, "html.parser")

        _preprocess_math_tags(soup)
        _format_spacing_and_blocks(soup)

        raw_text = soup.get_text(separator="")
        return _normalize_whitespace(raw_text)
    except Exception as exc:  # noqa: BLE001 — bs4 has no fixed exception
        # surface for malformed HTML; any failure here should degrade to
        # the raw HTML rather than break the whole sync.
        logger.warning("html_to_plain_text_conversion_failed", error=str(exc))
        return raw_html
