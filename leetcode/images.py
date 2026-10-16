from dataclasses import dataclass
from urllib.parse import urlparse

import requests

from .exceptions import TransportError


@dataclass(frozen=True)
class DownloadedImage:
    content: bytes
    extension: str


def _extension(url: str, content_type: str | None) -> str:
    """URL path extension first, then Content-Type, then 'png'."""
    path = urlparse(url).path
    if "." in path:
        ext = path.rsplit(".", 1)[-1].lower()
        if ext.isalnum() and len(ext) <= 4:
            return ext
    if content_type and "image/" in content_type:
        ext = content_type.split("image/")[-1].split(";")[0].strip()
        if ext.isalnum() and len(ext) <= 4:
            return "jpg" if ext == "jpeg" else ext
    return "png"


class ImageDownloader:
    def __init__(self, session: requests.Session, timeout: float = 10.0):
        self._session = session
        self._timeout = timeout

    def download(self, url: str) -> DownloadedImage:
        try:
            response = self._session.get(url, timeout=self._timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise TransportError(f"image download failed: {url}: {exc}") from exc
        return DownloadedImage(
            content=response.content,
            extension=_extension(url, response.headers.get("Content-Type")),
        )
