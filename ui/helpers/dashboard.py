from notes.models import NoteRecord
from notes.state import NoteState, classify_note, dir_key
from prefill.models import AIResponse
from problems.models import Problem, Submission
from userconfig import effective


def get_solved_problems_metrics() -> dict:
    """Calculate live solved statistics and difficulty breakdown for Dashboard Box 1."""
    total_problems = Problem.objects.count()
    total_solved = Problem.objects.filter(solved=True).count()

    overall_pct = (
        round((total_solved / total_problems) * 100, 1) if total_problems > 0 else 0.0
    )

    solved_easy = Problem.objects.filter(
        solved=True, difficulty=Problem.Difficulty.EASY
    ).count()
    total_easy = Problem.objects.filter(
        difficulty=Problem.Difficulty.EASY
    ).count()
    pct_easy = (
        round((solved_easy / total_easy) * 100, 1) if total_easy > 0 else 0.0
    )

    solved_medium = Problem.objects.filter(
        solved=True, difficulty=Problem.Difficulty.MEDIUM
    ).count()
    total_medium = Problem.objects.filter(
        difficulty=Problem.Difficulty.MEDIUM
    ).count()
    pct_medium = (
        round((solved_medium / total_medium) * 100, 1) if total_medium > 0 else 0.0
    )

    solved_hard = Problem.objects.filter(
        solved=True, difficulty=Problem.Difficulty.HARD
    ).count()
    total_hard = Problem.objects.filter(
        difficulty=Problem.Difficulty.HARD
    ).count()
    pct_hard = (
        round((solved_hard / total_hard) * 100, 1) if total_hard > 0 else 0.0
    )

    return {
        "total_problems": total_problems,
        "total_solved": total_solved,
        "overall_progress_pct": overall_pct,
        "easy": {
            "solved": solved_easy,
            "total": total_easy,
            "pct": pct_easy,
        },
        "medium": {
            "solved": solved_medium,
            "total": total_medium,
            "pct": pct_medium,
        },
        "hard": {
            "solved": solved_hard,
            "total": total_hard,
            "pct": pct_hard,
        },
    }


def get_notes_created_metrics() -> dict:
    """Calculate live notes count in the active vault output directory for Dashboard Box 2."""
    dirs = effective.output_dirs()
    output_dir_str = str(dirs[0]) if dirs else ""
    total_solved = Problem.objects.filter(solved=True).count()

    if not output_dir_str:
        return {
            "notes_created": 0,
            "total_solved": total_solved,
            "coverage_pct": 0.0,
            "pending_count": total_solved,
            "output_dir_configured": False,
        }

    canonical_dir = dir_key(output_dir_str)
    records = NoteRecord.objects.filter(
        output_dir=canonical_dir
    ).select_related("problem")

    problems_with_notes = set()
    for record in records:
        state = classify_note(record.content_hash, record.full_path)
        if state != NoteState.MISSING and record.problem.solved:
            problems_with_notes.add(record.problem_id)

    notes_created = len(problems_with_notes)
    coverage_pct = (
        round((notes_created / total_solved) * 100, 1) if total_solved > 0 else 0.0
    )
    pending_count = max(0, total_solved - notes_created)

    return {
        "notes_created": notes_created,
        "total_solved": total_solved,
        "coverage_pct": coverage_pct,
        "pending_count": pending_count,
        "output_dir_configured": True,
    }


def get_ai_prefills_metrics() -> dict:
    """Calculate live AI prefill response counts for Dashboard Box 3."""
    total = AIResponse.objects.count()
    active = AIResponse.objects.filter(is_active=True).count()
    problems_covered = AIResponse.objects.values("problem_id").distinct().count()

    return {
        "total": total,
        "active": active,
        "problems_covered": problems_covered,
    }


def get_needs_attention_metrics() -> dict:
    """Gather action items and categories that require user review for Dashboard Box 4."""
    categories = []

    # Category 1: Stale Submissions (newer solution available on LeetCode)
    stale_count = Submission.objects.filter(is_stale=True, pinned=False).count()
    if stale_count > 0:
        categories.append({
            "id": "stale_submissions",
            "label": "Stale Submissions",
            "count": stale_count,
            "badge": "Review",
            "badge_color": "amber",
            "description": f"{stale_count} newer solution(s) awaiting comparison",
            "href": "/problems/?stale=true",
        })

    pinned_count = Submission.objects.filter(is_stale=True, pinned=True).count()
    total_count = sum(c["count"] for c in categories)

    return {
        "total": total_count,
        "has_items": total_count > 0,
        "categories": categories,
        "pinned_with_updates": pinned_count,
    }


def get_dashboard_metrics() -> dict:
    """Aggregate all dynamic KPI metrics for Dashboard workspace."""
    return {
        "solved": get_solved_problems_metrics(),
        "notes": get_notes_created_metrics(),
        "prefills": get_ai_prefills_metrics(),
        "attention": get_needs_attention_metrics(),
    }
