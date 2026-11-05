from django.core.paginator import Paginator
from django.db.models import Exists, Max, OuterRef, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from leetcode.bootstrap import get_client
from leetcode.exceptions import AuthError, LeetCodeError, NotConfigured, RateLimitError
from notes.models import NoteRecord
from pipeline.actions import (
    ActionError,
    approve_all_reviews,
    approve_review,
    build_prompt_for,
    reject_review,
    retry_failed_item,
    submit_manual_reply,
)
from pipeline.models import ItemStage, Task, TaskItem
from pipeline.runtime.worker import ensure_worker_running
from pipeline.stages.definitions import STAGE_ORDER, Stage
from pipeline.tasks.control import cancel, pause, resume, start_task
from pipeline.tasks.creation import NothingToDo, create_task, default_task_name
from pipeline.tasks.eligibility import busy_problem_ids
from pipeline.tasks.preview import preview_plan
from pipeline.tasks.settings import MANUAL_PROVIDER, TaskSettingsError
from prefill.models import AIResponse
from prefill.providers.config import default_provider_name
from problems.models import Problem, Submission, SyncTask
from problems.services.sync import discover, refresh_recent_submissions

STAGE_ACTION_LABELS = {
    Stage.SAVE_NOTES.value: "Note saved",
    Stage.GENERATE_PREFILL.value: "Prefill generated",
    Stage.REVIEW.value: "Note reviewed & approved",
    Stage.FETCH_PROBLEM.value: "Problem fetched",
    Stage.FETCH_IMAGES.value: "Images fetched",
    Stage.FETCH_SUBMISSION.value: "Submission fetched",
}


def _get_dashboard_context():
    solved_count = Problem.objects.filter(solved=True).count()
    ai_prefills_count = AIResponse.objects.filter(
        is_active=True, status=AIResponse.Status.VALID
    ).count()
    notes_created_count = (
        NoteRecord.objects.values("problem_id")
        .distinct()
        .count()
    )

    # Needs attention counts
    failed_items = TaskItem.objects.filter(status=TaskItem.Status.FAILED).select_related(
        "problem", "task"
    )
    failed_count = failed_items.count()

    stale_submissions = Problem.objects.filter(
        solved=True,
        sync_tasks__status=SyncTask.Status.PENDING,
        sync_tasks__part=SyncTask.Part.SUBMISSIONS,
    ).distinct()
    stale_count = stale_submissions.count()

    needs_attention_count = failed_count + stale_count

    # Solved problems that do not have notes on disk yet
    missing_notes_count = max(0, solved_count - notes_created_count)

    # Active or latest Task
    active_task = (
        Task.objects.filter(status__in=[Task.Status.ACTIVE, Task.Status.PAUSED])
        .order_by("-created_at")
        .first()
    )
    if active_task is None:
        active_task = Task.objects.order_by("-created_at").first()

    task_stats = None
    if active_task:
        total_items = active_task.items.count()
        completed_items = active_task.items.filter(
            status=TaskItem.Status.COMPLETED
        ).count()
        running_items = active_task.items.filter(
            status=TaskItem.Status.RUNNING
        ).count()
        waiting_items = active_task.items.filter(
            status=TaskItem.Status.NEEDS_INPUT
        ).count()
        progress_percent = (
            int((completed_items / total_items * 100)) if total_items > 0 else 0
        )
        task_stats = {
            "task": active_task,
            "total_items": total_items,
            "completed_items": completed_items,
            "running_items": running_items,
            "waiting_items": waiting_items,
            "progress_percent": progress_percent,
        }

    # Recent activity
    recent_stage_rows = (
        ItemStage.objects.filter(status=ItemStage.Status.DONE)
        .select_related("item__problem")
        .order_by("-finished_at")[:6]
    )
    recent_activity = []
    for row in recent_stage_rows:
        recent_activity.append(
            {
                "problem": row.item.problem,
                "action": STAGE_ACTION_LABELS.get(row.stage, row.stage.replace("_", " ").title()),
                "timestamp": row.finished_at,
            }
        )

    return {
        "active": "dashboard",
        "solved_count": solved_count,
        "ai_prefills_count": ai_prefills_count,
        "notes_created_count": notes_created_count,
        "needs_attention_count": needs_attention_count,
        "missing_notes_count": missing_notes_count,
        "stale_count": stale_count,
        "failed_count": failed_count,
        "task_stats": task_stats,
        "recent_activity": recent_activity,
    }


@require_GET
def dashboard(request):
    context = _get_dashboard_context()
    return render(request, "ui/dashboard.html", context)


@require_GET
def tasks_progress_partial(request):
    """HTMX polling endpoint for the active task card."""
    context = _get_dashboard_context()
    return render(request, "ui/partials/dashboard_task_card.html", context)


@require_POST
def sync_leetcode(request):
    """Sync LeetCode data:
    1. Runs discover(client) to fetch all problem metadata, create new problem records, and update solved flags.
    2. Runs refresh_recent_submissions(client) to check recent accepted submissions and mark stale submissions.
    3. Returns notification toast + out-of-band update of the library stats cards!
    """
    try:
        client = get_client()
        disc_res = discover(client)
        sub_res = refresh_recent_submissions(client, limit=20)

        parts = []
        if disc_res.new_problems > 0:
            parts.append(f"{disc_res.new_problems} new problems found")
        total_newly_solved = disc_res.newly_solved + sub_res.newly_solved
        if total_newly_solved > 0:
            parts.append(f"{total_newly_solved} newly solved")
        if sub_res.reset > 0:
            parts.append(f"{sub_res.reset} submissions updated (marked stale)")
        if not parts:
            parts.append(f"Checked {disc_res.total} catalog problems and {sub_res.checked} recent submissions. Everything up to date!")

        msg = "Sync complete: " + ", ".join(parts)
        toast_type = "success"
    except (AuthError, NotConfigured) as exc:
        msg = f"LeetCode credentials not configured or expired: {exc}"
        toast_type = "error"
    except RateLimitError:
        msg = "LeetCode rate limit reached. Please wait a minute and retry."
        toast_type = "warning"
    except Exception as exc:
        msg = f"Sync failed: {exc}"
        toast_type = "error"

    context = _get_dashboard_context()
    context.update({"message": msg, "toast_type": toast_type})

    return render(request, "ui/partials/sync_response.html", context)


@require_GET
def generate_notes_view(request):
    """Dedicated page for selecting solved algorithm problems to generate notes for.
    Combines suggested (recently submitted, sorted by submission date descending)
    and remaining solved problems into a single unified table.
    Only includes problems in the Algorithms category.
    """
    query = request.GET.get("q", "").strip()
    difficulty = request.GET.get("difficulty", "").strip()
    notes_filter = request.GET.get("notes", "").strip()
    page_number = request.GET.get("page", 1)

    has_note_subquery = NoteRecord.objects.filter(
        problem_id=OuterRef("pk")
    )
    has_draft_subquery = AIResponse.objects.filter(
        problem_id=OuterRef("pk"), is_active=True, status=AIResponse.Status.VALID
    )

    # 1. Suggested / Recently Solved Problems (up to 20), sorted by latest submission_date descending
    # Strictly restricted to solved problems in the Algorithms category
    recent_submissions = (
        Submission.objects.filter(
            problem__solved=True,
            problem__category=Problem.Category.ALGORITHMS,
        )
        .values("problem_id")
        .annotate(latest_sub=Max("submission_date"))
        .order_by("-latest_sub")[:20]
    )
    recent_problem_pids = [item["problem_id"] for item in recent_submissions]
    sub_dates = {item["problem_id"]: item["latest_sub"] for item in recent_submissions}

    recent_problems_dict = {
        p.pk: p
        for p in Problem.objects.filter(
            pk__in=recent_problem_pids,
            category=Problem.Category.ALGORITHMS,
        ).annotate(
            has_note=Exists(has_note_subquery),
            has_draft=Exists(has_draft_subquery),
        )
    }

    suggested_problems = []
    for pid in recent_problem_pids:
        if pid in recent_problems_dict:
            p = recent_problems_dict[pid]
            p.latest_submission_date = sub_dates.get(pid)

            # Apply difficulty filter to suggested
            if difficulty and p.difficulty != difficulty:
                continue

            # Apply notes filter to suggested
            if notes_filter == "has_note" and not p.has_note:
                continue
            elif notes_filter == "has_draft" and not (p.has_draft and not p.has_note):
                continue
            elif notes_filter == "no_note" and (p.has_note or p.has_draft):
                continue

            suggested_problems.append(p)

    # 2. Other Solved Problems (All solved algorithm problems minus recently solved ones to prevent duplicates)
    base_qs = Problem.objects.filter(
        solved=True,
        category=Problem.Category.ALGORITHMS,
    ).annotate(
        has_note=Exists(has_note_subquery),
        has_draft=Exists(has_draft_subquery),
    )

    if query:
        # If searching, search across all solved algorithm problems
        other_solved_qs = base_qs
        if query.isdigit():
            other_solved_qs = other_solved_qs.filter(
                Q(leetcode_id=int(query))
                | Q(title__icontains=query)
                | Q(slug__icontains=query)
            )
        else:
            other_solved_qs = other_solved_qs.filter(
                Q(title__icontains=query) | Q(slug__icontains=query)
            )
    else:
        # Exclude the suggested ones shown at the top of page 1
        other_solved_qs = base_qs.exclude(pk__in=recent_problem_pids)

    if difficulty:
        other_solved_qs = other_solved_qs.filter(difficulty=difficulty)

    if notes_filter == "has_note":
        other_solved_qs = other_solved_qs.filter(has_note=True)
    elif notes_filter == "has_draft":
        other_solved_qs = other_solved_qs.filter(has_draft=True, has_note=False)
    elif notes_filter == "no_note":
        other_solved_qs = other_solved_qs.filter(has_note=False, has_draft=False)

    other_solved_qs = other_solved_qs.order_by("leetcode_id")

    paginator = Paginator(other_solved_qs, 25)
    page_obj = paginator.get_page(page_number)
    busy_ids = busy_problem_ids(page_obj.object_list)

    # Show the suggested section on page 1 when not actively searching
    try:
        is_page_one = int(page_number) == 1
    except (ValueError, TypeError):
        is_page_one = True

    show_suggested = is_page_one and not query and bool(suggested_problems)

    context = {
        "active": "generate",
        "suggested_problems": suggested_problems if show_suggested else [],
        "show_suggested": show_suggested,
        "page_obj": page_obj,
        "total_solved_count": Problem.objects.filter(
            solved=True, category=Problem.Category.ALGORITHMS
        ).count(),
        "query": query,
        "difficulty": difficulty,
        "notes_filter": notes_filter,
        "difficulties": Problem.Difficulty.values,
        "is_filtered": bool(query or difficulty or notes_filter),
        "busy_ids": busy_ids,
    }

    if request.headers.get("HX-Request"):
        return render(request, "ui/partials/generate_table.html", context)

    return render(request, "ui/generate_notes.html", context)


def task_preview_modal(request):
    """Renders the Pre-Run Confirmation Modal with preflight plan summary and task options."""
    if request.method == "POST":
        raw_ids = request.POST.getlist("problem_ids") or request.POST.getlist("selected_problems")
    else:
        raw_ids = request.GET.getlist("problem_ids") or request.GET.getlist("selected_problems")

    try:
        pids = [int(x) for x in raw_ids if str(x).isdigit()]
    except (ValueError, TypeError):
        pids = []

    problems = list(
        Problem.objects.filter(id__in=pids, solved=True, category=Problem.Category.ALGORITHMS)
        .order_by("leetcode_id")
    )

    if not problems:
        return HttpResponse(
            "<div class='p-4 text-center text-xs text-rose-500 dark:text-rose-400'>No valid solved algorithm problems selected.</div>"
        )

    preview = preview_plan(problems)
    cached_fetch_count = preview.eligible - preview.needs_fetching
    prefill_count = preview.stage_runs.get(Stage.GENERATE_PREFILL, len(problems))
    default_name = default_task_name(len(problems))

    try:
        def_provider = default_provider_name()
    except Exception:
        def_provider = MANUAL_PROVIDER

    context = {
        "problems": problems,
        "preview": preview,
        "cached_fetch_count": max(0, cached_fetch_count),
        "prefill_count": prefill_count,
        "default_name": default_name,
        "default_provider": def_provider,
        "is_automated_available": def_provider != MANUAL_PROVIDER,
    }
    return render(request, "ui/partials/task_preview_modal.html", context)


@require_POST
def create_task_action(request):
    """Creates a Task from submitted modal form, starts the worker, and redirects to task detail."""
    raw_ids = request.POST.getlist("problem_ids")
    try:
        pids = [int(x) for x in raw_ids if str(x).isdigit()]
    except (ValueError, TypeError):
        pids = []

    problems = list(
        Problem.objects.filter(id__in=pids, solved=True, category=Problem.Category.ALGORITHMS)
    )
    if not problems:
        return HttpResponse("No valid problems chosen.", status=400)

    task_name = request.POST.get("name", "").strip() or default_task_name(len(problems))
    provider = request.POST.get("provider", MANUAL_PROVIDER).strip()
    review = request.POST.get("review") == "on"

    refetch_toggle = request.POST.get("refetch_toggle") == "on"
    refetch_problem = refetch_toggle and request.POST.get("refetch_problem") == "on"
    refetch_images = refetch_toggle and request.POST.get("refetch_images") == "on"
    refetch_submission = refetch_toggle and request.POST.get("refetch_submission") == "on"

    replace_edited = request.POST.get("replace_edited") == "on"

    overrides = {
        "provider": provider,
        "review": review,
        "refetch": False,
        "refetch_problem": refetch_problem,
        "refetch_images": refetch_images,
        "refetch_submission": refetch_submission,
        "replace_edited": replace_edited,
    }

    try:
        creation = create_task(problems, overrides=overrides, name=task_name)
        task = creation.task
        ensure_worker_running()
    except (NothingToDo, TaskSettingsError, Exception) as exc:
        return HttpResponse(f"Error creating task: {exc}", status=400)

    redirect_url = reverse("ui:task_detail", args=[task.id])
    if request.headers.get("HX-Request"):
        response = HttpResponse(status=200)
        response["HX-Redirect"] = redirect_url
        return response

    return redirect("ui:task_detail", task_id=task.id)


def _format_task_title(name: str, count: int) -> str:
    if not name or ("·" in name and ("problem" in name or "problems" in name)):
        return f"Selected problems ({count})" if count > 1 else "Notes generation"
    return name


def _enrich_task_item(item: TaskItem):
    if item.status == TaskItem.Status.NEEDS_INPUT and item.input_kind == TaskItem.InputKind.MANUAL_PREFILL:
        try:
            item.manual_prompt = build_prompt_for(item).combined
        except Exception:
            item.manual_prompt = ""
    else:
        item.manual_prompt = ""

    item.latest_submission = item.problem.submissions.order_by("-submission_date").first()
    item.ai_draft = AIResponse.objects.filter(problem=item.problem, is_active=True).first()

    stages_map = {s.stage: s for s in item.stages.all()}
    item.stage_fetch_problem = stages_map.get(Stage.FETCH_PROBLEM)
    item.stage_fetch_images = stages_map.get(Stage.FETCH_IMAGES)
    item.stage_fetch_submission = stages_map.get(Stage.FETCH_SUBMISSION)
    item.stage_generate_prefill = stages_map.get(Stage.GENERATE_PREFILL)
    item.stage_review = stages_map.get(Stage.REVIEW)
    item.stage_save_notes = stages_map.get(Stage.SAVE_NOTES)

    # Prerequisite stages (1-3): if completed or skipped, they count as done
    s1_done = bool(item.stage_fetch_problem and item.stage_fetch_problem.status in (ItemStage.Status.DONE, ItemStage.Status.SKIPPED))
    s2_done = bool(item.stage_fetch_images and item.stage_fetch_images.status in (ItemStage.Status.DONE, ItemStage.Status.SKIPPED))
    s3_done = bool(item.stage_fetch_submission and item.stage_fetch_submission.status in (ItemStage.Status.DONE, ItemStage.Status.SKIPPED))
    s4_done = bool(item.stage_generate_prefill and item.stage_generate_prefill.status == ItemStage.Status.DONE)
    s5_done = bool(s4_done or (item.ai_draft and item.ai_draft.status == AIResponse.Status.VALID))
    s6_done = bool(item.stage_save_notes and item.stage_save_notes.status == ItemStage.Status.DONE)

    completed_count = sum([s1_done, s2_done, s3_done, s4_done, s5_done, s6_done])
    item.completed_stages_count = completed_count

    is_s4_waiting = item.status == TaskItem.Status.NEEDS_INPUT and item.input_kind == TaskItem.InputKind.MANUAL_PREFILL
    is_s6_waiting = item.status == TaskItem.Status.NEEDS_INPUT and item.input_kind == TaskItem.InputKind.REVIEW

    def get_seg_color(is_done, stage_obj, is_waiting=False):
        if is_done:
            return "bg-zinc-900 dark:bg-zinc-100"
        if is_waiting or (stage_obj and stage_obj.status == ItemStage.Status.WAITING):
            return "bg-amber-500"
        if stage_obj:
            if stage_obj.status == ItemStage.Status.RUNNING:
                return "bg-blue-500 animate-pulse"
            if stage_obj.status == ItemStage.Status.FAILED:
                return "bg-rose-500"
        return "bg-zinc-200 dark:bg-zinc-800"

    item.segments = [
        {"color": get_seg_color(s1_done, item.stage_fetch_problem)},
        {"color": get_seg_color(s2_done, item.stage_fetch_images)},
        {"color": get_seg_color(s3_done, item.stage_fetch_submission)},
        {"color": get_seg_color(s4_done, item.stage_generate_prefill, is_waiting=is_s4_waiting)},
        {"color": get_seg_color(s5_done, None)},
        {"color": get_seg_color(s6_done, item.stage_save_notes, is_waiting=is_s6_waiting)},
    ]

    # Subtitles and badges for stages matching mockup:
    # Stage 1:
    if s1_done:
        item.stage_1_subtitle = "Fetched and parsed successfully"
        item.stage_1_badge = "Skipped" if (item.stage_fetch_problem and item.stage_fetch_problem.status == ItemStage.Status.SKIPPED) else "Complete"
    elif item.stage_fetch_problem and item.stage_fetch_problem.status == ItemStage.Status.RUNNING:
        item.stage_1_subtitle = "Fetching problem details from LeetCode..."
        item.stage_1_badge = "Running"
    elif item.stage_fetch_problem and item.stage_fetch_problem.status == ItemStage.Status.FAILED:
        item.stage_1_subtitle = "Failed to fetch description"
        item.stage_1_badge = "Failed"
    else:
        item.stage_1_subtitle = "Waiting in pipeline"
        item.stage_1_badge = "Pending"

    # Stage 2:
    if s2_done:
        item.stage_2_subtitle = "No images to download" if (item.stage_fetch_images and item.stage_fetch_images.status == ItemStage.Status.SKIPPED) else "Images downloaded"
        item.stage_2_badge = "Complete" if (item.stage_fetch_images and item.stage_fetch_images.status == ItemStage.Status.DONE) else "Skipped"
    elif item.stage_fetch_images and item.stage_fetch_images.status == ItemStage.Status.RUNNING:
        item.stage_2_subtitle = "Downloading problem images..."
        item.stage_2_badge = "Running"
    elif item.stage_fetch_images and item.stage_fetch_images.status == ItemStage.Status.FAILED:
        item.stage_2_subtitle = "Failed to download images"
        item.stage_2_badge = "Failed"
    else:
        item.stage_2_subtitle = "Waiting in pipeline"
        item.stage_2_badge = "Pending"

    # Stage 3:
    if item.latest_submission:
        sub_date = item.latest_submission.submission_date.strftime("%b %d, %Y") if item.latest_submission.submission_date else ""
        item.stage_3_subtitle = f"{item.latest_submission.lang.capitalize()} · Submitted {sub_date}"
        item.stage_3_badge = "Ready"
    elif s3_done:
        item.stage_3_subtitle = "Submission fetched"
        item.stage_3_badge = "Ready"
    elif item.stage_fetch_submission and item.stage_fetch_submission.status == ItemStage.Status.RUNNING:
        item.stage_3_subtitle = "Fetching accepted submission..."
        item.stage_3_badge = "Running"
    elif item.stage_fetch_submission and item.stage_fetch_submission.status == ItemStage.Status.FAILED:
        item.stage_3_subtitle = "No accepted submission found"
        item.stage_3_badge = "Failed"
    else:
        item.stage_3_subtitle = "Waiting in pipeline"
        item.stage_3_badge = "Pending"

    # Stage 4:
    if is_s4_waiting:
        item.stage_4_subtitle = "Waiting for your response"
        item.stage_4_badge = "Action required"
    elif s4_done:
        item.stage_4_subtitle = "Draft generated successfully"
        item.stage_4_badge = "Complete"
    elif item.stage_generate_prefill and item.stage_generate_prefill.status == ItemStage.Status.RUNNING:
        item.stage_4_subtitle = "Generating solution draft..."
        item.stage_4_badge = "Running"
    elif item.stage_generate_prefill and item.stage_generate_prefill.status == ItemStage.Status.FAILED:
        item.stage_4_subtitle = "AI generation failed"
        item.stage_4_badge = "Failed"
    else:
        item.stage_4_subtitle = "Waiting in pipeline"
        item.stage_4_badge = "Pending"

    # Stage 5:
    if s5_done:
        item.stage_5_subtitle = "JSON schema validated successfully"
        item.stage_5_badge = "Complete"
    elif item.stage_generate_prefill and item.stage_generate_prefill.status == ItemStage.Status.RUNNING:
        item.stage_5_subtitle = "Validating against JSON schema..."
        item.stage_5_badge = "Running"
    else:
        item.stage_5_subtitle = "Waiting for AI response"
        item.stage_5_badge = "Pending"

    # Stage 6:
    if s6_done:
        item.stage_6_subtitle = "Note written to disk successfully"
        item.stage_6_badge = "Complete"
    elif is_s6_waiting:
        item.stage_6_subtitle = "Note draft ready for review"
        item.stage_6_badge = "Action required"
    elif item.stage_save_notes and item.stage_save_notes.status == ItemStage.Status.RUNNING:
        item.stage_6_subtitle = "Writing markdown note to disk..."
        item.stage_6_badge = "Running"
    else:
        item.stage_6_subtitle = "Waiting for validation"
        item.stage_6_badge = "Pending"

    # Plain English status text & badges
    if item.status == TaskItem.Status.COMPLETED:
        item.plain_status_text = "Completed"
        item.status_badge_label = "Completed"
        item.status_badge_class = "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-800/60 dark:bg-emerald-950/60 dark:text-emerald-400"
    elif item.status == TaskItem.Status.NEEDS_INPUT:
        if item.input_kind == TaskItem.InputKind.MANUAL_PREFILL:
            item.plain_status_text = "Waiting for manual AI input"
            item.status_badge_label = "Awaiting input"
        elif item.input_kind == TaskItem.InputKind.REVIEW:
            item.plain_status_text = "Waiting for note review"
            item.status_badge_label = "Awaiting review"
        else:
            item.plain_status_text = "Waiting for input"
            item.status_badge_label = "Awaiting input"
        item.status_badge_class = "border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-800/60 dark:bg-amber-950/60 dark:text-amber-500"
    elif item.status == TaskItem.Status.FAILED:
        item.plain_status_text = f"Stage failed: {item.error or 'error'}"
        item.status_badge_label = "Failed"
        item.status_badge_class = "border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-800/60 dark:bg-rose-950/60 dark:text-rose-400"
    elif item.status == TaskItem.Status.RUNNING:
        item.plain_status_text = f"Running {item.stage.replace('_', ' ')}"
        item.status_badge_label = "Running"
        item.status_badge_class = "border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-800/60 dark:bg-blue-950/60 dark:text-blue-400"
    else:
        item.plain_status_text = "Queued in pipeline"
        item.status_badge_label = "Queued"
        item.status_badge_class = "border-zinc-200 bg-zinc-100 text-zinc-600 dark:border-zinc-700 dark:bg-zinc-800/60 dark:text-zinc-400"

    return item


def _compute_task_status_info(task, total_items, completed_items, waiting_items, failed_items):
    """Computes plain status, status label, badge classes, and indicators for a task."""
    if task.status == Task.Status.CANCELLED:
        return {
            "computed_status": "cancelled",
            "status_label": "Cancelled",
            "status_badge_class": "border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-900/60 dark:bg-rose-950/40 dark:text-rose-400",
            "dot_class": "bg-rose-500",
            "is_pulse": False,
        }
    if task.status == Task.Status.FINISHED:
        if failed_items == total_items and total_items > 0:
            return {
                "computed_status": "failed",
                "status_label": "Failed",
                "status_badge_class": "border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-900/60 dark:bg-rose-950/40 dark:text-rose-400",
                "dot_class": "bg-rose-500",
                "is_pulse": False,
            }
        elif failed_items > 0:
            return {
                "computed_status": "completed_with_errors",
                "status_label": "Completed with errors",
                "status_badge_class": "border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/40 dark:text-amber-400",
                "dot_class": "bg-amber-500",
                "is_pulse": False,
            }
        else:
            return {
                "computed_status": "completed",
                "status_label": "Completed",
                "status_badge_class": "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900/60 dark:bg-emerald-950/40 dark:text-emerald-400",
                "dot_class": "bg-emerald-500",
                "is_pulse": False,
            }
    if task.status == Task.Status.PAUSED:
        return {
            "computed_status": "paused",
            "status_label": "Paused",
            "status_badge_class": "border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-900/60 dark:bg-amber-950/40 dark:text-amber-400",
            "dot_class": "bg-amber-500",
            "is_pulse": False,
        }
    if waiting_items > 0:
        return {
            "computed_status": "waiting_for_input",
            "status_label": "Waiting for input",
            "status_badge_class": "border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-800/60 dark:bg-amber-950/60 dark:text-amber-500",
            "dot_class": "bg-amber-500",
            "is_pulse": False,
        }
    if failed_items == total_items and total_items > 0:
        return {
            "computed_status": "failed",
            "status_label": "Failed",
            "status_badge_class": "border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-900/60 dark:bg-rose-950/40 dark:text-rose-400",
            "dot_class": "bg-rose-500",
            "is_pulse": False,
        }
    return {
        "computed_status": "running",
        "status_label": "Running",
        "status_badge_class": "border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-900/60 dark:bg-blue-950/40 dark:text-blue-400",
        "dot_class": "bg-blue-500",
        "is_pulse": True,
    }


def _get_task_detail_context(task_id: int):
    task = get_object_or_404(Task, pk=task_id)
    items = (
        task.items.select_related("problem")
        .prefetch_related("stages", "problem__submissions")
        .order_by("position")
    )

    total_items = items.count()
    completed_items = items.filter(status=TaskItem.Status.COMPLETED).count()
    waiting_items = items.filter(status=TaskItem.Status.NEEDS_INPUT).count()
    failed_items = items.filter(status=TaskItem.Status.FAILED).count()
    running_items = items.filter(status=TaskItem.Status.RUNNING).count()
    progress_percent = int((completed_items / total_items * 100)) if total_items > 0 else 0

    # Specific status
    status_info = _compute_task_status_info(
        task, total_items, completed_items, waiting_items, failed_items
    )
    computed_status = status_info["computed_status"]
    status_label = status_info["status_label"]
    status_badge_class = status_info["status_badge_class"]

    task_display_title = _format_task_title(task.name, total_items)

    for item in items:
        _enrich_task_item(item)

    problem_ids = [item.problem_id for item in items]
    submissions = Submission.objects.filter(problem_id__in=problem_ids)
    has_submissions = submissions.exists()
    all_submissions_pinned = has_submissions and not submissions.filter(pinned=False).exists()

    queued_items = items.filter(status=TaskItem.Status.QUEUED).count()
    has_active_work = bool(task.status == Task.Status.ACTIVE)

    return {
        "active": "tasks",
        "task": task,
        "task_display_title": task_display_title,
        "computed_status": computed_status,
        "status_label": status_label,
        "status_badge_class": status_badge_class,
        "items": items,
        "total_items": total_items,
        "completed_items": completed_items,
        "waiting_items": waiting_items,
        "failed_items": failed_items,
        "running_items": running_items,
        "progress_percent": progress_percent,
        "stages_order": STAGE_ORDER,
        "all_submissions_pinned": all_submissions_pinned,
        "has_active_work": has_active_work,
    }


@require_GET
def task_detail(request, task_id: int):
    """Detailed view of an individual task and all its problem stages with live updates and interactive controls."""
    context = _get_task_detail_context(task_id)
    if request.headers.get("HX-Request"):
        return render(request, "ui/partials/task_detail_content.html", context)

    return render(request, "ui/task_detail.html", context)


@require_POST
def toggle_submission_pin(request, submission_id: int):
    """Toggles the pinned status of an accepted submission."""
    submission = get_object_or_404(Submission, pk=submission_id)
    submission.pinned = not submission.pinned
    submission.save(update_fields=["pinned"])
    return render(request, "ui/partials/submission_pin_button.html", {"submission": submission})


@require_POST
def task_item_manual_input(request, item_id: int):
    """Submits manual AI response pasted by user."""
    item = get_object_or_404(TaskItem.objects.select_related("task", "problem"), pk=item_id)
    reply_text = request.POST.get("reply_text", "").strip()
    error_msg = None
    try:
        submit_manual_reply(item_id, reply_text)
        ensure_worker_running()
    except ActionError as exc:
        error_msg = str(exc)

    if request.headers.get("HX-Request"):
        context = _get_task_detail_context(item.task_id)
        updated_item = next((it for it in context["items"] if it.id == item_id), item)
        context["item"] = updated_item
        context["manual_error"] = error_msg
        return render(request, "ui/partials/task_item_action_response.html", context)

    return redirect("ui:task_detail", task_id=item.task_id)


@require_POST
def task_item_retry(request, item_id: int):
    """Retries a failed task item stage."""
    item = get_object_or_404(TaskItem.objects.select_related("task", "problem"), pk=item_id)
    try:
        retry_failed_item(item_id)
        ensure_worker_running()
    except Exception:
        pass
    if request.headers.get("HX-Request"):
        context = _get_task_detail_context(item.task_id)
        updated_item = next((it for it in context["items"] if it.id == item_id), item)
        context["item"] = updated_item
        return render(request, "ui/partials/task_item_action_response.html", context)

    return redirect("ui:task_detail", task_id=item.task_id)


@require_POST
def task_item_approve_review(request, item_id: int):
    """Approves an AI draft waiting for review."""
    item = get_object_or_404(TaskItem.objects.select_related("task", "problem"), pk=item_id)
    try:
        approve_review(item_id)
        ensure_worker_running()
    except Exception:
        pass
    if request.headers.get("HX-Request"):
        context = _get_task_detail_context(item.task_id)
        updated_item = next((it for it in context["items"] if it.id == item_id), item)
        context["item"] = updated_item
        return render(request, "ui/partials/task_item_action_response.html", context)

    return redirect("ui:task_detail", task_id=item.task_id)


@require_POST
def task_item_reject_review(request, item_id: int):
    """Rejects an AI draft waiting for review, re-queuing prefill."""
    item = get_object_or_404(TaskItem.objects.select_related("task", "problem"), pk=item_id)
    try:
        reject_review(item_id)
        ensure_worker_running()
    except Exception:
        pass
    if request.headers.get("HX-Request"):
        context = _get_task_detail_context(item.task_id)
        updated_item = next((it for it in context["items"] if it.id == item_id), item)
        context["item"] = updated_item
        return render(request, "ui/partials/task_item_action_response.html", context)

    return redirect("ui:task_detail", task_id=item.task_id)


@require_POST
def task_approve_all_reviews(request, task_id: int):
    """Approves all items waiting for review in this task."""
    try:
        approve_all_reviews(task_id)
        ensure_worker_running()
    except Exception:
        pass
    if request.headers.get("HX-Request"):
        context = _get_task_detail_context(task_id)
        return render(request, "ui/partials/task_detail_content.html", context)
    return redirect("ui:task_detail", task_id=task_id)


@require_GET
def task_live_status(request, task_id: int):
    """Lightweight polling endpoint that updates only live stats and running items."""
    context = _get_task_detail_context(task_id)
    return render(request, "ui/partials/task_live_updates.html", context)


@require_POST
def task_pin_all_submissions(request, task_id: int):
    """Toggle pin on all submissions in task: unpin all if all are pinned, else pin all."""
    task = get_object_or_404(Task, pk=task_id)
    problem_ids = list(task.items.values_list("problem_id", flat=True))
    submissions = Submission.objects.filter(problem_id__in=problem_ids)
    if submissions.exists() and not submissions.filter(pinned=False).exists():
        submissions.update(pinned=False)
    else:
        submissions.update(pinned=True)

    if request.headers.get("HX-Request"):
        context = _get_task_detail_context(task_id)
        return render(request, "ui/partials/task_pin_response.html", context)
    return redirect("ui:task_detail", task_id=task_id)


@require_POST
def task_batch_pin_submissions(request, task_id: int):
    """Pins or unpins submissions for a selected list of task items."""
    task = get_object_or_404(Task, pk=task_id)
    item_ids = request.POST.getlist("item_ids")
    pin_action = request.POST.get("pin_action", "pin")  # "pin" or "unpin"

    if item_ids:
        items = task.items.filter(id__in=item_ids)
        problem_ids = list(items.values_list("problem_id", flat=True))
        submissions = Submission.objects.filter(problem_id__in=problem_ids)
        if pin_action == "unpin":
            submissions.update(pinned=False)
        else:
            submissions.update(pinned=True)

    if request.headers.get("HX-Request"):
        context = _get_task_detail_context(task_id)
        return render(request, "ui/partials/task_pin_response.html", context)
    return redirect("ui:task_detail", task_id=task_id)


@require_POST
def task_pause_action(request, task_id: int):
    """Pauses an active task."""
    try:
        pause(task_id)
    except Exception:
        pass
    if request.headers.get("HX-Request"):
        context = _get_task_detail_context(task_id)
        return render(request, "ui/partials/task_control_response.html", context)
    return redirect("ui:task_detail", task_id=task_id)


@require_POST
def task_resume_action(request, task_id: int):
    """Resumes a paused task and wakes the worker."""
    try:
        resume(task_id)
        ensure_worker_running()
    except Exception:
        pass
    if request.headers.get("HX-Request"):
        context = _get_task_detail_context(task_id)
        return render(request, "ui/partials/task_control_response.html", context)
    return redirect("ui:task_detail", task_id=task_id)


@require_POST
def task_cancel_action(request, task_id: int):
    """Cancels an active or paused task, releasing its problems."""
    try:
        cancel(task_id)
    except Exception:
        pass
    if request.headers.get("HX-Request"):
        context = _get_task_detail_context(task_id)
        return render(request, "ui/partials/task_control_response.html", context)
    return redirect("ui:task_detail", task_id=task_id)


def _get_tasks_list_context(request):
    page_number = request.GET.get("page", 1)
    status_filter = request.GET.get("status", "").strip()

    qs = Task.objects.all().order_by("-created_at")
    if status_filter:
        qs = qs.filter(status=status_filter)

    total_tasks_count = qs.count()
    paginator = Paginator(qs, 15)
    page_obj = paginator.get_page(page_number)

    tasks_data = []
    has_active_tasks = False
    for task in page_obj:
        items = task.items.all()
        total_items = items.count()
        completed_items = items.filter(status=TaskItem.Status.COMPLETED).count()
        running_items = items.filter(status=TaskItem.Status.RUNNING).count()
        waiting_items = items.filter(status=TaskItem.Status.NEEDS_INPUT).count()
        failed_items = items.filter(status=TaskItem.Status.FAILED).count()
        progress_pct = int((completed_items / total_items * 100)) if total_items > 0 else 0
        display_title = _format_task_title(task.name, total_items)
        status_info = _compute_task_status_info(
            task, total_items, completed_items, waiting_items, failed_items
        )

        # Check if task is active or paused (uncompleted, needs polling)
        is_active = task.status in [Task.Status.ACTIVE, Task.Status.PAUSED]
        if is_active:
            has_active_tasks = True

        tasks_data.append({
            "task": task,
            "display_title": display_title,
            "total_items": total_items,
            "completed_items": completed_items,
            "running_items": running_items,
            "waiting_items": waiting_items,
            "failed_items": failed_items,
            "progress_percent": progress_pct,
            "is_active": is_active,
            "status_info": status_info,
        })

    return {
        "active": "tasks",
        "tasks_data": tasks_data,
        "page_obj": page_obj,
        "status_filter": status_filter,
        "total_tasks_count": total_tasks_count,
        "has_active_tasks": has_active_tasks,
    }


@require_GET
def tasks_list(request):
    """List of all generation tasks, their statuses, progress, and date."""
    context = _get_tasks_list_context(request)
    return render(request, "ui/tasks_list.html", context)


@require_GET
def tasks_live_status(request):
    """Lightweight polling endpoint to sync active/paused/failed task rows in all tasks list."""
    context = _get_tasks_list_context(request)
    return render(request, "ui/partials/tasks_live_updates.html", context)


@require_GET
def problems_list(request):
    query = request.GET.get("q", "").strip()
    difficulty = request.GET.get("difficulty", "").strip()
    status = request.GET.get("status", "").strip()
    category = request.GET.get("category", "").strip()
    notes_filter = request.GET.get("notes", "").strip()
    page_number = request.GET.get("page", 1)

    has_note_subquery = NoteRecord.objects.filter(
        problem_id=OuterRef("pk")
    )
    has_draft_subquery = AIResponse.objects.filter(
        problem_id=OuterRef("pk"), is_active=True, status=AIResponse.Status.VALID
    )

    qs = Problem.objects.all().annotate(
        has_note=Exists(has_note_subquery),
        has_draft=Exists(has_draft_subquery),
    )

    if query:
        if query.isdigit():
            qs = qs.filter(
                Q(leetcode_id=int(query))
                | Q(title__icontains=query)
                | Q(slug__icontains=query)
            )
        else:
            qs = qs.filter(Q(title__icontains=query) | Q(slug__icontains=query))

    if difficulty:
        qs = qs.filter(difficulty=difficulty)

    if status == "solved":
        qs = qs.filter(solved=True)
    elif status == "unsolved":
        qs = qs.filter(solved=False)

    if category:
        qs = qs.filter(category=category)

    if notes_filter == "has_note":
        qs = qs.filter(has_note=True)
    elif notes_filter == "has_draft":
        qs = qs.filter(has_draft=True, has_note=False)
    elif notes_filter == "no_note":
        qs = qs.filter(has_note=False, has_draft=False)

    qs = qs.order_by("leetcode_id")

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(page_number)

    # Check which problems on this page are busy in an active task
    busy_ids = busy_problem_ids(page_obj.object_list)

    context = {
        "active": "problems",
        "page_obj": page_obj,
        "query": query,
        "difficulty": difficulty,
        "status": status,
        "category": category,
        "notes_filter": notes_filter,
        "difficulties": Problem.Difficulty.values,
        "categories": Problem.Category.values,
        "busy_ids": busy_ids,
        "total_count": paginator.count,
    }

    if request.headers.get("HX-Request"):
        return render(request, "ui/partials/problems_table.html", context)

    return render(request, "ui/problems.html", context)
