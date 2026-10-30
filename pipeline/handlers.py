"""One small function per pipeline stage. A stage returns an outcome, or raises if it fails."""

from collections.abc import Callable
from dataclasses import dataclass

from leetcode.bootstrap import get_client, get_image_downloader
from leetcode.client import LeetCodeClient
from leetcode.images import ImageDownloader
from prefill.providers.base import Provider
from prefill.providers.registry import get_provider
from problems.models import Problem, SyncTask
from problems.services.sync import run_part

from .models import TaskItem
from .outcomes import StageError, StageOutcome, done
from .task_settings import TaskSettings


@dataclass(frozen=True)
class StageDeps:
    """The outside world a stage can use. Tests swap these for fakes."""

    get_client: Callable[[], LeetCodeClient] = get_client
    get_downloader: Callable[[], ImageDownloader] = get_image_downloader
    get_provider: Callable[[str], Provider] = get_provider


def current_problem(item: TaskItem) -> Problem:
    """Load the problem again, so a stage sees what the stages before it saved."""
    return Problem.objects.get(pk=item.problem_id)


def fetch_part(item: TaskItem, deps: StageDeps, part: str) -> StageOutcome:
    """Fetch one part of the problem. LeetCode errors are left for the engine to handle."""
    run_part(current_problem(item), part, deps.get_client(), deps.get_downloader())
    return done()


def fetch_problem(
    item: TaskItem, settings: TaskSettings, deps: StageDeps
) -> StageOutcome:
    return fetch_part(item, deps, SyncTask.Part.CONTENT)


def fetch_images(
    item: TaskItem, settings: TaskSettings, deps: StageDeps
) -> StageOutcome:
    if not current_problem(item).raw_question_html:
        raise StageError("the problem's statement hasn't been fetched yet")
    return fetch_part(item, deps, SyncTask.Part.IMAGES)


def fetch_submission(
    item: TaskItem, settings: TaskSettings, deps: StageDeps
) -> StageOutcome:
    return fetch_part(item, deps, SyncTask.Part.SUBMISSIONS)
