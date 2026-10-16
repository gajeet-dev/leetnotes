import html
import re

import structlog
from bs4 import BeautifulSoup
from markdownify import MarkdownConverter

logger = structlog.get_logger(__name__)


class LeetCodeMarkdownConverter(MarkdownConverter):
    """Custom Markdown converter tailored for LeetCode problem descriptions."""

    def convert_code(self, el, text, parent_tags):
        # Prevent double-wrapping if code is inside pre
        if el.parent and el.parent.name == "pre":
            return text
        code_text = text.strip()
        return f"`{code_text}`" if code_text else ""

    def convert_sub(self, el, text, parent_tags):
        return f"_{text}" if text else ""

    def convert_sup(self, el, text, parent_tags):
        return f"^{text}" if text else ""

    def convert_pre(self, el, text, parent_tags):
        # Extract code block content cleanly
        code_content = text.strip()
        return f"\n\n```\n{code_content}\n```\n\n"

    def convert_font(self, el, text, parent_tags):
        # LeetCode wraps trailing spaces in <font face="monospace">&nbsp;</font>
        return text


def html_to_markdown(raw_html: str | None) -> str:
    """Converts LeetCode question HTML into clean Markdown."""
    if not raw_html:
        return ""
    try:
        # 1. Unescape HTML entities (&nbsp;, &lt;, &gt;)
        cleaned_html = html.unescape(raw_html)
        cleaned_html = cleaned_html.replace("\xa0", " ")  # replace non-breaking spaces

        # 2. Pre-process DOM to clean specific tags before converting
        soup = BeautifulSoup(cleaned_html, "html.parser")

        # Normalize <sup> and <sub> tags before markdown conversion
        for sub in soup.find_all("sub"):
            sub.string = f"_{sub.get_text()}"
            sub.unwrap()
        for sup in soup.find_all("sup"):
            sup.string = f"^{sup.get_text()}"
            sup.unwrap()

        # 3. Convert modified HTML tree to Markdown
        converter = LeetCodeMarkdownConverter(
            heading_style="ATX",
            bullets="-",
            strip=["script", "style"],
        )
        md = converter.convert(str(soup))

        # 4. Post-processing cleanup
        # Fix multiple blank lines (max 2 newlines)
        md = re.sub(r"\n{3,}", "\n\n", md)

        return md.strip()
    except Exception as exc:  # noqa: BLE001 — bs4/markdownify have no fixed
        # exception surface for malformed HTML; any failure here should
        # degrade to the raw HTML rather than break the whole sync.
        logger.warning("html_to_markdown_conversion_failed", error=str(exc))
        return raw_html
