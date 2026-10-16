from typing import Any

import requests

from ..exceptions import (
    AuthError,
    RateLimitError,
    ResponseShapeError,
    TransportError,
)


class HttpClient:
    def __init__(self, session: requests.Session, base_url: str):
        self._session = session
        self._base_url = base_url.rstrip("/")

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self._base_url}{path}"
        try:
            response = self._session.request(method, url, **kwargs)
        except requests.RequestException as exc:
            raise TransportError(f"{method} {path} failed: {exc}") from exc

        status = response.status_code
        if status in (401, 403):
            raise AuthError(f"HTTP {status}: refresh your LeetCode cookies")
        if status == 429:
            raise RateLimitError("HTTP 429 after retries")
        if status >= 400:
            raise TransportError(f"HTTP {status} on {method} {path}")

        try:
            return response.json()
        except ValueError as exc:
            raise ResponseShapeError(f"{path} did not return valid JSON") from exc

    def get_json(self, path: str, **kwargs: Any) -> Any:
        return self._request("GET", path, **kwargs)

    def post_json(self, path: str, payload: dict[str, Any], **kwargs: Any) -> Any:
        return self._request("POST", path, json=payload, **kwargs)
