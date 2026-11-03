"""What a stage can use from the outside world, and how it loads the problem it works on."""

from collections.abc import Callable
from dataclasses import dataclass

from leetcode.bootstrap import get_client, get_image_downloader
from leetcode.client import LeetCodeClient
from leetcode.images import ImageDownloader
from prefill.providers.base import Provider
from prefill.providers.registry import get_provider
from problems.models import Problem

from ..models import TaskItem


@dataclass(frozen=True)
class StageDeps:
    """The outside world a stage can use. Tests swap these for fakes."""

    get_client: Callable[[], LeetCodeClient] = get_client
    get_downloader: Callable[[], ImageDownloader] = get_image_downloader
    get_provider: Callable[[str], Provider] = get_provider


def current_problem(item: TaskItem) -> Problem:
    """Load the problem again, so a stage sees what the stages before it saved."""
    return Problem.objects.get(pk=item.problem_id)
