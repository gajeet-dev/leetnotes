"""Builds the LeetCode client from the current settings."""

import threading
from dataclasses import dataclass, field
from functools import cache

import requests
from django.conf import settings

from leetcode.resources.account import AccountResource
from userconfig import effective

from .client import LeetCodeClient
from .exceptions import NotConfigured
from .images import ImageDownloader
from .resources.problems import ProblemsResource
from .resources.submissions import SubmissionsResource
from .transport.graphql import GraphQLClient
from .transport.http import HttpClient
from .transport.session import SessionConfig, create_image_session, create_session

NOT_CONFIGURED_MESSAGE = (
    "LeetCode cookies are not set. Add them in Settings "
    "(or LEETCODE_SESSION and LEETCODE_CSRF_TOKEN in .env)."
)


@dataclass(frozen=True)
class ClientSettings:
    """Everything the client is built from. Equal values mean the same client can be reused."""

    session: str = field(repr=False)  # repr=False keeps the cookies out of logs
    csrf_token: str = field(repr=False)
    base_url: str
    requests_per_second: float
    requests_per_minute: float
    username: str


def current_client_settings() -> ClientSettings:
    session = effective.leetcode_session()
    csrf_token = effective.leetcode_csrf_token()
    if not (session and csrf_token):
        raise NotConfigured(NOT_CONFIGURED_MESSAGE)
    return ClientSettings(
        session=session,
        csrf_token=csrf_token,
        base_url=settings.LEETCODE_BASE_URL,
        requests_per_second=effective.requests_per_second(),
        requests_per_minute=effective.requests_per_minute(),
        username=effective.leetcode_username(),
    )


def build_session(config: ClientSettings) -> requests.Session:
    return create_session(
        SessionConfig(
            session_cookie=config.session,
            csrf_token=config.csrf_token,
            base_url=config.base_url,
            requests_per_second=config.requests_per_second,
            requests_per_minute=config.requests_per_minute,
        )
    )


def build_client(config: ClientSettings) -> LeetCodeClient:
    http = HttpClient(build_session(config), config.base_url)
    gql = GraphQLClient(http)
    return LeetCodeClient(
        problems=ProblemsResource(gql, http),
        submissions=SubmissionsResource(gql, default_username=config.username),
        account=AccountResource(gql),
    )


_lock = threading.Lock()
_cached: tuple[ClientSettings, LeetCodeClient] | None = None


def get_client() -> LeetCodeClient:
    """The shared client (one session, one rate limiter), rebuilt when its settings change."""
    global _cached
    config = current_client_settings()
    with _lock:
        if _cached is None or _cached[0] != config:
            _cached = (config, build_client(config))
        return _cached[1]


def reset_client() -> None:
    """Forget the cached client. Mainly for tests."""
    global _cached
    with _lock:
        _cached = None


@cache
def get_image_downloader() -> ImageDownloader:
    return ImageDownloader(create_image_session(settings.LEETCODE_BASE_URL))
