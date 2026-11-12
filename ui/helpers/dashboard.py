from problems.models import Problem, Submission


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
        "attention": get_needs_attention_metrics(),
    }
