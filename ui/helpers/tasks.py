import datetime
import math
from typing import Any

import markdown
import structlog
from django.core.paginator import Paginator
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from leetcode.bootstrap import get_client
from leetcode.client import LeetCodeClient
from notes.models import NoteRecord
from notes.state import NoteState, classify_note, dir_key
from pipeline.actions import build_prompt_for
from pipeline.models import ItemStage, PipelineItem
from pipeline.stages.definitions import STAGE_ORDER, Stage
from pipeline.tasks.eligibility import busy_problem_ids
from prefill.models import AIResponse
from prefill.providers.config import default_provider_name
from problems.models import Problem
from userconfig import effective, load_config

logger = structlog.get_logger(__name__)


def render_ai_response_to_html(problem: Problem, resp: AIResponse | None) -> str:
    """Render an AI response parsed solution note to HTML preview using the solution note template."""
    if not resp:
        return "<div class='p-4 text-center text-sm text-zinc-500 italic'>No AI response generated yet.</div>"

    from notes.context import build_solution_context
    from notes.rendering import render_template
    from prefill.parsing import parse_solution
    from prefill.schema import SolutionNote

    note = None
    if resp.parsed:
        if isinstance(resp.parsed, dict):
            try:
                note = SolutionNote.model_validate(resp.parsed)
            except Exception as e:
                logger.debug("validate_parsed_solution_note_failed", error=str(e))
        elif isinstance(resp.parsed, str):
            try:
                note = SolutionNote.model_validate_json(resp.parsed)
            except Exception as e:
                logger.debug("validate_parsed_solution_json_str_failed", error=str(e))

    if note is None and resp.raw_text:
        try:
            note = parse_solution(resp.raw_text)
        except Exception as e:
            logger.debug("parse_raw_solution_note_failed", error=str(e))

    if note is not None:
        try:
            ctx = build_solution_context(problem, note)
            md_text = render_template("solution.md.j2", ctx)
            return markdown.markdown(md_text, extensions=["meta", "fenced_code", "tables"])
        except Exception as e:
            logger.warning("render_solution_template_failed", error=str(e))

    if resp.raw_text:
        raw = resp.raw_text.strip()
        if raw.startswith("{") or raw.startswith("["):
            try:
                import json
                pretty = json.dumps(json.loads(raw), indent=2)
                return f"<div class='p-3 text-xs text-amber-600 dark:text-amber-400 border border-amber-200 dark:border-amber-800 rounded bg-amber-50 dark:bg-amber-950/40 mb-2'>Showing raw unparsed draft JSON</div><pre class='p-3 rounded bg-zinc-900 text-zinc-100 font-mono text-xs overflow-x-auto'><code>{pretty}</code></pre>"
            except Exception:
                pass
        return markdown.markdown(resp.raw_text, extensions=["meta", "fenced_code", "tables"])

    return "<div class='p-4 text-center text-sm text-zinc-500 italic'>No AI response content available.</div>"


def get_problems_batch_status(problem_pks: list[int]) -> dict[int, dict]:
    """Efficient batch lookup of note saved status and pipeline item status."""
    problems = list(Problem.objects.filter(pk__in=problem_pks))
    output_folders = effective.output_dirs()
    target_output_dir = str(output_folders[0]) if output_folders else None

    note_records_by_problem: dict[int, list[NoteRecord]] = {}
    if target_output_dir and problems:
        canonical_key = dir_key(target_output_dir)
        records = NoteRecord.objects.filter(
            problem__in=problems,
            output_dir=canonical_key,
        )
        for r in records:
            note_records_by_problem.setdefault(r.problem_id, []).append(r)

    latest_items: dict[int, PipelineItem] = {}
    if problems:
        for item in PipelineItem.objects.filter(problem__in=problems).order_by(
            "-created_at"
        ):
            if item.problem_id not in latest_items:
                latest_items[item.problem_id] = item

    results = {}
    for p in problems:
        recs = note_records_by_problem.get(p.pk, [])
        note_saved = False
        for r in recs:
            state = classify_note(r.content_hash, r.full_path)
            if state != NoteState.MISSING and r.full_path.exists():
                note_saved = True
                break

        item = latest_items.get(p.pk)
        pipeline_status = item.status if item else None
        input_kind = item.input_kind if item else ""
        item_id = item.pk if item else None

        results[p.pk] = {
            "slug": p.slug,
            "leetcode_id": p.leetcode_id,
            "note_saved": note_saved,
            "pipeline_status": pipeline_status,
            "pipeline_item_id": item_id,
            "input_kind": input_kind,
        }

    return results


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
    status_filter: str = "",
    page: int | str = 1,
    per_page: int = 100,
    client: LeetCodeClient | None = None,
):
    """Fetch solved Algorithms problems for the Catalogue with live Recent submissions and verified note status."""
    has_note_subquery = NoteRecord.objects.filter(problem_id=OuterRef("pk"))

    search_query = (search_query or "").strip()
    difficulty = (difficulty or "").strip()
    status_filter = (status_filter or "").strip()

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
        # Search active: show matching in a single paginated list
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

            if p.category != Problem.Category.ALGORITHMS:
                continue

            if not p.solved:
                p.solved = True
                p.save(update_fields=["solved", "updated_at"])

            recent_algo_problems.append(p)
            recent_algo_pks.add(p.pk)

        # Apply difficulty filter if specified
        recent_list = []
        for p in recent_algo_problems:
            if difficulty and p.difficulty.lower() != difficulty.lower():
                continue
            if recent_timestamps.get(p.slug):
                p.submission_date = recent_timestamps[p.slug]
            recent_list.append(p)

        recent_total = len(recent_list)

        # Other problems matching current filters
        other_qs = qs.exclude(pk__in=recent_algo_pks).order_by("leetcode_id")
        other_total = other_qs.count()
        total_count = recent_total + other_total

        page_1_other_count = min(max(0, per_page - recent_total), other_total)
        remaining_other_count = other_total - page_1_other_count

        if remaining_other_count > 0:
            subsequent_pages = math.ceil(remaining_other_count / per_page)
            num_pages = 1 + subsequent_pages
        else:
            num_pages = 1

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

    # Output directory verification for NoteRecord
    output_folders = effective.output_dirs()
    target_output_dir = str(output_folders[0]) if output_folders else None

    note_records_by_problem: dict[int, list[NoteRecord]] = {}
    if target_output_dir and all_visible:
        canonical_key = dir_key(target_output_dir)
        records = NoteRecord.objects.filter(
            problem__in=all_visible,
            output_dir=canonical_key,
        )
        for r in records:
            note_records_by_problem.setdefault(r.problem_id, []).append(r)

    # Attach pipeline item and verified note status to each visible problem
    latest_items: dict[int, PipelineItem] = {}
    if all_visible:
        for item in PipelineItem.objects.filter(problem__in=all_visible).order_by(
            "-created_at"
        ):
            if item.problem_id not in latest_items:
                latest_items[item.problem_id] = item

    for p in all_visible:
        p.is_busy = p.pk in busy_ids
        if not getattr(p, "submission_date", None):
            p.submission_date = (
                p.submission.submission_date
                if hasattr(p, "submission") and p.submission
                else None
            )
        p.pipeline_item = latest_items.get(p.pk)

        # Note existence verified against disk:
        p.note_saved = False
        recs = note_records_by_problem.get(p.pk, [])
        for r in recs:
            state = classify_note(r.content_hash, r.full_path)
            if state != NoteState.MISSING and r.full_path.exists():
                p.note_saved = True
                break

    has_active_items = any(
        p.pipeline_item
        and p.pipeline_item.status
        in (
            PipelineItem.Status.QUEUED,
            PipelineItem.Status.RUNNING,
            PipelineItem.Status.NEEDS_INPUT,
        )
        for p in all_visible
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
        "has_active_items": has_active_items,
    }


def get_workbench_pipeline_items(
    search_query: str = "",
    status_filter: str = "",
    date_filter: str = "",
    page: int | str = 1,
    per_page: int = 50,
) -> dict[str, Any]:
    """Fetch pipeline items for the Notes Generation Workbench with KPIs, filters, and stages."""
    search_query = (search_query or "").strip()
    status_filter = (status_filter or "").strip()
    date_filter = (date_filter or "").strip()

    qs = PipelineItem.objects.select_related(
        "problem", "problem__submission"
    ).prefetch_related("stages")

    # Global KPI counts across all pipeline items
    kpi_counts = {
        "total": PipelineItem.objects.count(),
        "queued": PipelineItem.objects.filter(
            status=PipelineItem.Status.QUEUED
        ).count(),
        "running": PipelineItem.objects.filter(
            status=PipelineItem.Status.RUNNING
        ).count(),
        "action_required": PipelineItem.objects.filter(
            status=PipelineItem.Status.NEEDS_INPUT
        ).count(),
        "review_waiting": PipelineItem.objects.filter(
            status=PipelineItem.Status.NEEDS_INPUT,
            input_kind=PipelineItem.InputKind.REVIEW,
        ).count(),
        "completed": PipelineItem.objects.filter(
            status=PipelineItem.Status.COMPLETED
        ).count(),
        "failed": PipelineItem.objects.filter(
            status=PipelineItem.Status.FAILED
        ).count(),
    }

    # Search filter
    if search_query:
        qs = qs.filter(
            Q(problem__title__icontains=search_query)
            | Q(problem__slug__icontains=search_query)
            | Q(problem__leetcode_id__icontains=search_query)
        )

    # Status filter
    if status_filter == "active":
        qs = qs.filter(
            status__in=[
                PipelineItem.Status.QUEUED,
                PipelineItem.Status.RUNNING,
                PipelineItem.Status.NEEDS_INPUT,
                PipelineItem.Status.PAUSED,
            ]
        )
    elif status_filter in ("needs_input", "action_required"):
        qs = qs.filter(status=PipelineItem.Status.NEEDS_INPUT)
    elif status_filter == "completed":
        qs = qs.filter(status=PipelineItem.Status.COMPLETED)
    elif status_filter == "failed":
        qs = qs.filter(status=PipelineItem.Status.FAILED)
    elif status_filter == "queued":
        qs = qs.filter(status=PipelineItem.Status.QUEUED)
    elif status_filter == "running":
        qs = qs.filter(status=PipelineItem.Status.RUNNING)

    # Date filter
    now = timezone.now()
    if date_filter == "today":
        today_start = timezone.datetime.combine(
            timezone.localdate(),
            datetime.time.min,
            tzinfo=timezone.get_current_timezone(),
        )
        qs = qs.filter(created_at__gte=today_start)
    elif date_filter == "last_7_days":
        qs = qs.filter(created_at__gte=now - datetime.timedelta(days=7))
    elif date_filter == "last_30_days":
        qs = qs.filter(created_at__gte=now - datetime.timedelta(days=30))
    elif date_filter == "older":
        qs = qs.filter(created_at__lt=now - datetime.timedelta(days=30))

    qs = qs.order_by("-updated_at", "-created_at")

    try:
        page_num = int(page)
    except ValueError, TypeError:
        page_num = 1

    try:
        per_page = int(per_page)
    except ValueError, TypeError:
        per_page = 50

    paginator = Paginator(qs, per_page)
    page_obj = paginator.get_page(page_num)

    # Attach stage list in order to each visible item
    for item in page_obj.object_list:
        stages_by_name = {s.stage: s for s in item.stages.all()}
        item.stage_list = [stages_by_name.get(st) for st in STAGE_ORDER]

    has_active_items = any(
        item.status in (PipelineItem.Status.QUEUED, PipelineItem.Status.RUNNING)
        for item in page_obj.object_list
    )
    has_review_items = kpi_counts["review_waiting"] > 0

    return {
        "page_obj": page_obj,
        "items": page_obj.object_list,
        "kpi_counts": kpi_counts,
        "total_count": paginator.count,
        "display_start": page_obj.start_index() if paginator.count > 0 else 0,
        "display_end": page_obj.end_index() if paginator.count > 0 else 0,
        "has_active_items": has_active_items,
        "has_review_items": has_review_items,
        "stages_order": STAGE_ORDER,
        "search_query": search_query,
        "status_filter": status_filter,
        "date_filter": date_filter,
    }


def get_modal_data_for_item_stage(item: PipelineItem, stage_name: str) -> dict:
    """Prepare context data for stage-specific action modals."""
    problem = item.problem
    stage_row = item.stages.filter(stage=stage_name).first()

    context = {
        "item": item,
        "problem": problem,
        "stage_name": stage_name,
        "stage_row": stage_row,
    }

    if stage_name == Stage.FETCH_SUBMISSION:
        sub = getattr(problem, "submission", None)
        newer_sub = {
            "submission_id": getattr(sub, "newer_submission_id", None),
            "submission_date": getattr(sub, "newer_submission_date", None),
            "lang": getattr(sub, "lang", "Unknown"),
            "code": "",
        }
        if sub and sub.newer_submission_id:
            try:
                client = get_client()
                detail = client.get_submission_details(sub.newer_submission_id)
                if detail:
                    newer_sub["lang"] = getattr(detail, "lang", None) or sub.lang
                    newer_sub["code"] = getattr(detail, "code", None) or ""
                    sub_dt = getattr(detail, "submission_date", None)
                    if isinstance(sub_dt, (datetime.date, datetime.datetime)):
                        newer_sub["submission_date"] = sub_dt
            except Exception as exc:
                logger.warning(
                    "failed_fetching_newer_submission_preview", error=str(exc)
                )
        context["existing_submission"] = sub
        context["newer_submission"] = newer_sub

    elif stage_name == Stage.GENERATE_PREFILL:
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

        responses = list(
            AIResponse.objects.filter(problem=problem).order_by("-created_at")
        )
        context["responses"] = responses

        selected_response = next((r for r in responses if r.is_active), None) or (
            responses[0] if responses else None
        )
        context["selected_response"] = selected_response

        context["selected_preview_html"] = render_ai_response_to_html(
            problem, selected_response
        )

        preferred_tab = item.settings.get("preferred_prefill_tab") or "manual"
        context["preferred_tab"] = preferred_tab

        cfg = load_config()
        agy_models = (
            cfg.agy.available_models
            if hasattr(cfg, "agy") and cfg.agy.available_models
            else ["gemini-2.5-pro", "gemini-2.5-flash"]
        )
        claude_models = [
            "claude-3-7-sonnet",
            "claude-3-5-sonnet",
            "claude-3-5-haiku",
        ]
        context["agy_models"] = agy_models
        context["claude_models"] = claude_models
        context["default_provider"] = item.settings.get(
            "provider", default_provider_name()
        )

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
