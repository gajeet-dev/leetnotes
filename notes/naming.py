"""File names for generated notes: the one place that decides them."""

from problems.models import Problem


def _base_name(problem: Problem) -> str:
    # Zero-padded so the files sort numerically in the vault.
    return f"{problem.leetcode_id:04d}-{problem.slug}"


def problem_note_stem(problem: Problem) -> str:
    return _base_name(problem)


def problem_note_filename(problem: Problem) -> str:
    return f"{problem_note_stem(problem)}.md"


def solution_note_stem(problem: Problem) -> str:
    return f"{_base_name(problem)}-solution"


def solution_note_filename(problem: Problem) -> str:
    return f"{solution_note_stem(problem)}.md"
