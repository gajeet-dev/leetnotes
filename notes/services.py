from problems.models import Problem

from .context import build_problem_context
from .rendering import render_template


def render_problem_note(problem: Problem) -> str:
    return render_template("problem.md.j2", build_problem_context(problem))
