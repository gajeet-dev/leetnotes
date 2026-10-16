from collections.abc import Callable
from urllib.parse import urlparse

from bs4 import BeautifulSoup


def rewrite_images(
    html: str, resolve: Callable[[int, str], str | None]
) -> tuple[str, int]:
    """Rewrite every <img> in `html`.

    `resolve(index, src)` returns the new src, or None to drop the tag.
    Non-http(s) sources are dropped without calling `resolve`.
    Returns (new_html, total_img_count).
    """
    soup = BeautifulSoup(html, "html.parser")
    images = soup.find_all("img")

    for idx, img in enumerate(images):
        src = str(img.get("src", "")).strip()
        new_src = (
            resolve(idx, src) if urlparse(src).scheme in ("http", "https") else None
        )
        if new_src is None:
            img.decompose()
        else:
            img["src"] = new_src

    return str(soup), len(images)
