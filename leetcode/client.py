from collections.abc import Iterator

from .dto import (
    ProblemDTO,
    RecentAcDTO,
    SolvedProblemDTO,
    SubmissionDetailDTO,
    SubmissionSummaryDTO,
)
from .mappers.problem import map_problem, map_solved_problem
from .mappers.submission import (
    map_recent_ac,
    map_submission_detail,
    map_submission_summary,
)
from .resources.problems import ProblemsResource
from .resources.submissions import SubmissionsResource


class LeetCodeClient:
    """The only LeetCode entry point the rest of the project should use."""

    def __init__(
        self,
        problems: ProblemsResource,
        submissions: SubmissionsResource,
    ):
        self._problems = problems
        self._submissions = submissions

    def get_problem(self, slug: str) -> ProblemDTO:
        return map_problem(self._problems.get(slug))

    def list_solved_problems(self) -> list[SolvedProblemDTO]:
        """Return all problems currently marked as accepted."""
        return [map_solved_problem(pair) for pair in self._problems.list_solved()]

    def get_submissions(
        self, slug: str, *, limit: int = 20, offset: int = 0
    ) -> list[SubmissionSummaryDTO]:
        raw = self._submissions.list_page(slug, limit=limit, offset=offset)
        return [map_submission_summary(item) for item in raw]

    def iter_submissions(
        self, slug: str, *, page_size: int = 20
    ) -> Iterator[SubmissionSummaryDTO]:
        for item in self._submissions.iter_all(slug, page_size=page_size):
            yield map_submission_summary(item)

    def get_recent_accepted(
        self, username: str | None = None, *, limit: int = 20
    ) -> list[RecentAcDTO]:
        raw = self._submissions.recent_accepted(username, limit=limit)
        return [map_recent_ac(item) for item in raw]

    def get_submission_details(self, submission_id: int) -> SubmissionDetailDTO:
        return map_submission_detail(self._submissions.get_details(submission_id))
