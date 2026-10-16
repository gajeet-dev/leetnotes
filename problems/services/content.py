from leetcode.mappers.question_content.html_to_markdown import html_to_markdown
from leetcode.mappers.question_content.html_to_plain_text import html_to_plain_text

from ..models import Problem


def build_problem_content(problem: Problem) -> Problem:
    """Derive every converted content field from the stored HTML."""
    raw = problem.raw_question_html

    problem.content_remote_markdown = html_to_markdown(raw)
    problem.content_text = html_to_plain_text(raw)

    # Local markdown only exists once the images step has run:
    #   has_images True  -> convert the rewritten HTML
    #   has_images False -> no images to localize, so it equals the remote one
    #   has_images None  -> images not processed yet, leave it empty
    if problem.has_images:
        problem.content_local_markdown = html_to_markdown(problem.content_local_html)
    elif problem.has_images is False:
        problem.content_local_markdown = problem.content_remote_markdown
    else:
        problem.content_local_markdown = None

    problem.save(
        update_fields=[
            "content_remote_markdown",
            "content_local_markdown",
            "content_text",
            "updated_at",
        ]
    )
    return problem
