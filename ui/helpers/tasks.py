import math
from typing import Any

import markdown
import structlog
from django.core.paginator import Paginator
from django.db.models import Exists, OuterRef, Q

from leetcode.bootstrap import get_client
from leetcode.client import LeetCodeClient
from notes.models import NoteRecord
from pipeline.actions import build_prompt_for
from pipeline.models import ItemStage, Task, TaskItem
from pipeline.stages.definitions import STAGE_ORDER, Stage
from pipeline.tasks.eligibility import busy_problem_ids
from prefill.models import AIResponse
from problems.models import Problem

logger = structlog.get_logger(__name__)


class CustomPaginatorInfo:
    def __init__(self, num_pages: int, total_count: int):
        self.num_pages = max(1, num_pages)
        self.count = total_count


class CustomPage:
    def __init__(
        self, object_list: list, number: int, num_pages: int, total_count: int
    ):
        self.object_list = object_list
        self.number = number
        self.paginator = CustomPaginatorInfo(num_pages, total_count)

    def __len__(self):
        return len(self.object_list)

    def __iter__(self):
        return iter(self.object_list)

    def has_previous(self) -> bool:
        return self.number > 1

    def has_next(self) -> bool:
        return self.number < self.paginator.num_pages

    def previous_page_number(self) -> int:
        return max(1, self.number - 1)

    def next_page_number(self) -> int:
        return min(self.paginator.num_pages, self.number + 1)


def fetch_live_recent_accepted_submissions(
    client: LeetCodeClient | None = None,
    limit: int = 20,
) -> list[Any]:
    """Fetch live recent accepted submissions from the LeetCode API (no caching)."""
    try:
        lc_client = client or get_client()
        return lc_client.get_recent_accepted(limit=limit)
    except Exception as exc:
        logger.warning("live_recent_ac_fetch_failed", error=str(exc))
        return []


def get_algorithms_solved_problems(
    search_query: str = "",
    difficulty: str = "",
    page: int | str = 1,
    per_page: int = 100,
    client: LeetCodeClient | None = None,
):
    """Fetch solved Algorithms problems with pagination and live Recent accepted submissions."""
    has_note_subquery = NoteRecord.objects.filter(problem_id=OuterRef("pk"))

    search_query = (search_query or "").strip()
    difficulty = (difficulty or "").strip()

    qs = (
        Problem.objects.filter(
            solved=True,
            category=Problem.Category.ALGORITHMS,
        )
        .annotate(has_note=Exists(has_note_subquery))
        .select_related("submission")
    )

    if search_query:
        qs = qs.filter(
            Q(title__icontains=search_query)
            | Q(slug__icontains=search_query)
            | Q(leetcode_id__icontains=search_query)
        )

    if difficulty:
        qs = qs.filter(difficulty__iexact=difficulty)

    try:
        page_num = int(page)
    except ValueError, TypeError:
        page_num = 1

    try:
        per_page = int(per_page)
    except ValueError, TypeError:
        per_page = 100

    if search_query:
        # User is searching: do not separate into recent vs other; show all matching in a unified list
        recent_problems = []
        recent_total = 0
        paginator = Paginator(qs.order_by("leetcode_id"), per_page)
        page_obj = paginator.get_page(page_num)
        other_problems = list(page_obj.object_list)
        other_total = paginator.count
        total_count = other_total

        if total_count == 0:
            display_start = 0
            display_end = 0
        else:
            display_start = page_obj.start_index()
            display_end = page_obj.end_index()
    else:
        # Fetch live recent accepted submissions from the LeetCode API without caching
        recent_items = fetch_live_recent_accepted_submissions(
            client=client,
            limit=20,
        )

        seen_slugs = set()
        ordered_recent_slugs = []
        recent_timestamps = {}
        for item in recent_items:
            slug = item.slug if hasattr(item, "slug") else item.get("slug")
            ts = item.timestamp if hasattr(item, "timestamp") else item.get("timestamp")
            if slug and slug not in seen_slugs:
                seen_slugs.add(slug)
                ordered_recent_slugs.append(slug)
                recent_timestamps[slug] = ts

        # Look up matching problems by title slug from our local database to inspect category
        db_problems_by_slug = {
            p.slug: p
            for p in Problem.objects.filter(slug__in=ordered_recent_slugs)
            .annotate(has_note=Exists(has_note_subquery))
            .select_related("submission")
        }

        # Filter strictly by category: only keep problems from the Algorithms category
        recent_algo_problems = []
        recent_algo_pks = set()
        for slug in ordered_recent_slugs:
            p = db_problems_by_slug.get(slug)
            if p is None:
                continue

            # Check problem category from our local DB - only show Algorithms category questions
            if p.category != Problem.Category.ALGORITHMS:
                continue

            # Mark solved=True if accepted on LeetCode but not yet marked solved locally
            if not p.solved:
                p.solved = True
                p.save(update_fields=["solved", "updated_at"])

            recent_algo_problems.append(p)
            recent_algo_pks.add(p.pk)

        # Apply difficulty filter if specified (e.g. Easy, Medium, Hard)
        recent_list = []
        for p in recent_algo_problems:
            if difficulty and p.difficulty.lower() != difficulty.lower():
                continue
            if recent_timestamps.get(p.slug):
                p.submission_date = recent_timestamps[p.slug]
            recent_list.append(p)

        recent_total = len(recent_list)

        # Other problems matching current filters (excluding all live recent algorithms)
        other_qs = qs.exclude(pk__in=recent_algo_pks).order_by("leetcode_id")
        other_total = other_qs.count()
        total_count = recent_total + other_total

        # Page 1 displays up to `recent_total` recent problems,
        # plus enough other problems to fill up to `per_page` total items on Page 1.
        page_1_other_count = min(max(0, per_page - recent_total), other_total)
        remaining_other_count = other_total - page_1_other_count

        if remaining_other_count > 0:
            subsequent_pages = math.ceil(remaining_other_count / per_page)
            num_pages = 1 + subsequent_pages
        else:
            num_pages = 1

        # Bound page_num
        page_num = max(1, min(page_num, num_pages))

        if page_num == 1:
            recent_problems = recent_list
            if page_1_other_count > 0:
                other_problems = list(other_qs[:page_1_other_count])
            else:
                other_problems = []

            if total_count == 0:
                display_start = 0
                display_end = 0
            else:
                display_start = 1
                display_end = len(recent_problems) + len(other_problems)
        else:
            recent_problems = []
            start_offset = page_1_other_count + (page_num - 2) * per_page
            end_offset = start_offset + per_page
            other_problems = list(other_qs[start_offset:end_offset])

            display_start = recent_total + start_offset + 1
            display_end = min(total_count, display_start + len(other_problems) - 1)

        page_obj = CustomPage(
            object_list=other_problems
            if page_num > 1
            else (recent_problems + other_problems),
            number=page_num,
            num_pages=num_pages,
            total_count=total_count,
        )

    all_visible = recent_problems + other_problems
    busy_ids = busy_problem_ids(all_visible)

    for p in all_visible:
        p.is_busy = p.pk in busy_ids
        if not getattr(p, "submission_date", None):
            p.submission_date = (
                p.submission.submission_date
                if hasattr(p, "submission") and p.submission
                else None
            )

    return {
        "page_obj": page_obj,
        "recent_problems": recent_problems,
        "other_problems": other_problems,
        "recent_total": recent_total,
        "other_total": other_total,
        "total_count": total_count,
        "display_start": display_start,
        "display_end": display_end,
        "busy_ids": busy_ids,
        "is_search": bool(search_query),
    }


def get_task_summary(task: Task) -> dict:
    """Calculate progress, counts, and settings summary for a single Task."""
    items_qs = task.items.all()
    total_items = items_qs.count()

    completed_items = items_qs.filter(status=TaskItem.Status.COMPLETED).count()
    failed_items = items_qs.filter(status=TaskItem.Status.FAILED).count()
    waiting_items = items_qs.filter(status=TaskItem.Status.NEEDS_INPUT).count()
    running_items = items_qs.filter(status=TaskItem.Status.RUNNING).count()
    queued_items = items_qs.filter(status=TaskItem.Status.QUEUED).count()

    progress_pct = int(completed_items / total_items * 100) if total_items > 0 else 0

    settings = task.settings or {}
    provider_name = settings.get("provider", "Default AI")
    require_review = settings.get("require_review", False)
    output_dirs = settings.get("output_dirs", [])
    output_dir_str = output_dirs[0] if output_dirs else "Not configured"

    return {
        "task": task,
        "total_items": total_items,
        "completed_items": completed_items,
        "failed_items": failed_items,
        "waiting_items": waiting_items,
        "running_items": running_items,
        "queued_items": queued_items,
        "progress_pct": progress_pct,
        "provider_name": provider_name,
        "require_review": require_review,
        "output_dir": output_dir_str,
    }


def get_task_detail_data(task_id: int, stage_filter: str = "all") -> dict:
    """Fetch all task items and stages for the Task Detail view, applying optional filter."""
    task = Task.objects.get(pk=task_id)
    summary = get_task_summary(task)

    items_qs = (
        task.items.select_related("problem")
        .prefetch_related("stages")
        .order_by("position")
    )

    all_items = list(items_qs)

    # Compute filter counts from all items
    counts = {
        "all": len(all_items),
        "review": sum(
            1
            for i in all_items
            if i.status == TaskItem.Status.NEEDS_INPUT
            and i.input_kind == TaskItem.InputKind.REVIEW
        ),
        "prefill": sum(
            1
            for i in all_items
            if i.status == TaskItem.Status.NEEDS_INPUT
            and i.input_kind == TaskItem.InputKind.MANUAL_PREFILL
        ),
        "completed": sum(1 for i in all_items if i.status == TaskItem.Status.COMPLETED),
        "failed": sum(1 for i in all_items if i.status == TaskItem.Status.FAILED),
    }

    # Apply stage filter
    if stage_filter == "review":
        filtered_items = [
            i
            for i in all_items
            if i.status == TaskItem.Status.NEEDS_INPUT
            and i.input_kind == TaskItem.InputKind.REVIEW
        ]
    elif stage_filter == "prefill":
        filtered_items = [
            i
            for i in all_items
            if i.status == TaskItem.Status.NEEDS_INPUT
            and i.input_kind == TaskItem.InputKind.MANUAL_PREFILL
        ]
    elif stage_filter == "completed":
        filtered_items = [i for i in all_items if i.status == TaskItem.Status.COMPLETED]
    elif stage_filter == "failed":
        filtered_items = [i for i in all_items if i.status == TaskItem.Status.FAILED]
    else:
        filtered_items = all_items

    # Organize stages dict for each item for clean template lookup
    for item in filtered_items:
        stages_by_name = {s.stage: s for s in item.stages.all()}
        item.stage_list = [stages_by_name.get(st) for st in STAGE_ORDER]

    return {
        "task": task,
        "summary": summary,
        "items": filtered_items,
        "counts": counts,
        "stage_filter": stage_filter,
        "stages_order": STAGE_ORDER,
    }


def get_modal_data_for_item_stage(item: TaskItem, stage_name: str) -> dict:
    """Prepare context data for stage-specific action modals."""
    problem = item.problem
    stage_row = item.stages.filter(stage=stage_name).first()

    context = {
        "item": item,
        "problem": problem,
        "stage_name": stage_name,
        "stage_row": stage_row,
    }

    if stage_name == Stage.GENERATE_PREFILL:
        try:
            prompt_data = build_prompt_for(item)
            prompt_text = (
                prompt_data.combined
                if hasattr(prompt_data, "combined")
                else str(prompt_data)
            )
        except Exception as e:
            prompt_text = f"Error generating prompt: {e}"
        context["prompt_text"] = prompt_text

    elif stage_name == Stage.REVIEW:
        active_response = AIResponse.objects.filter(
            problem=problem, is_active=True
        ).first()
        raw_markdown = (
            active_response.raw_text if active_response else "No AI response found."
        )
        html_content = markdown.markdown(
            raw_markdown, extensions=["fenced_code", "tables"]
        )
        context["response"] = active_response
        context["raw_markdown"] = raw_markdown
        context["rendered_html"] = html_content

    elif stage_row and stage_row.status == ItemStage.Status.FAILED:
        context["error_message"] = (
            stage_row.error or item.error or "Unknown error occurred during this stage."
        )

    return context
