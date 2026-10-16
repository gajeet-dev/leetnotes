from functools import cache

import requests
from django.conf import settings

from .client import LeetCodeClient
from .images import ImageDownloader
from .resources.problems import ProblemsResource
from .resources.submissions import SubmissionsResource
from .transport.graphql import GraphQLClient
from .transport.http import HttpClient
from .transport.session import SessionConfig, create_image_session, create_session


def build_session() -> requests.Session:
    return create_session(
        SessionConfig(
            session_cookie=settings.LEETCODE_SESSION,
            csrf_token=settings.LEETCODE_CSRF_TOKEN,
            base_url=settings.LEETCODE_BASE_URL,
            requests_per_second=settings.LEETCODE_REQUESTS_PER_SECOND,
        )
    )


@cache
def get_client() -> LeetCodeClient:
    """Return the shared LeetCodeClient (one session, one rate limiter)."""
    http = HttpClient(build_session(), settings.LEETCODE_BASE_URL)
    gql = GraphQLClient(http)
    return LeetCodeClient(
        problems=ProblemsResource(gql, http),
        submissions=SubmissionsResource(
            gql, default_username=settings.LEETCODE_USERNAME
        ),
    )


@cache
def get_image_downloader() -> ImageDownloader:
    return ImageDownloader(create_image_session(settings.LEETCODE_BASE_URL))
