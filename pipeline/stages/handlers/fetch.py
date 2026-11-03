"""The fetch stages: get a problem's statement, images and submission from LeetCode."""

from pipeline.models import TaskItem
from pipeline.stages.deps import StageDeps, current_problem
from pipeline.stages.outcomes import StageError, StageOutcome, done
from pipeline.tasks.settings import TaskSettings
from problems.models import SyncTask
from problems.services.sync import run_part


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
