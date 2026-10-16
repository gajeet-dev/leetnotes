import random
import time
from dataclasses import dataclass

import requests
import structlog
from requests import PreparedRequest, Response
from requests_ratelimiter import LimiterAdapter
from urllib3.util import Retry

logger = structlog.get_logger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class SessionConfig:
    session_cookie: str
    csrf_token: str
    base_url: str = "https://leetcode.com"
    requests_per_second: float = 1.0
    timeout: float = 30.0
    max_retries: int = 3
    jitter_range: tuple[float, float] = (0.1, 0.6)


class JitteredLimiterAdapter(LimiterAdapter):
    """Rate-limiting adapter that adds random jitter and a default timeout."""

    def __init__(
        self,
        *args,
        jitter_range: tuple[float, float] = (0.1, 0.6),
        timeout: float = 30.0,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.jitter_range = jitter_range
        self.timeout = timeout

    def send(self, request: PreparedRequest, **kwargs) -> Response:
        # Session passes timeout=None explicitly, so setdefault() won't work.
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = self.timeout
        time.sleep(random.uniform(*self.jitter_range))
        return super().send(request, **kwargs)


def create_session(config: SessionConfig) -> requests.Session:
    """Create the HTTP session used by the LeetCode client."""
    session = requests.Session()

    retries = Retry(
        total=config.max_retries,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=None,  # retry POST too; our GraphQL queries are read-only
        raise_on_status=False,
    )

    adapter = JitteredLimiterAdapter(
        per_second=config.requests_per_second,
        max_retries=retries,
        jitter_range=config.jitter_range,
        timeout=config.timeout,
    )
    session.mount("https://", adapter)

    session.headers.update(
        {
            "X-CSRFToken": config.csrf_token,
            "Content-Type": "application/json",
            "Referer": config.base_url,
            "User-Agent": USER_AGENT,
        }
    )

    session.cookies.set(
        "LEETCODE_SESSION", config.session_cookie, domain=".leetcode.com"
    )
    session.cookies.set("csrftoken", config.csrf_token, domain=".leetcode.com")

    logger.info(
        "http_session_configured",
        requests_per_second=config.requests_per_second,
        jitter_range=config.jitter_range,
        max_retries=config.max_retries,
        timeout=config.timeout,
    )
    return session
