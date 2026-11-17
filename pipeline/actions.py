"""What you can do about a pipeline item that is waiting for you."""

from collections.abc import Callable, Sequence
from typing import Any

from django.db import transaction

from pipeline.engine import advance_item
from pipeline.models import ItemStage, PipelineItem
from pipeline.runtime.worker import ensure_worker_running
from pipeline.stages.definitions import Stage
from pipeline.stages.outcomes import done
from pipeline.tasks.transitions import complete_stage, save_fields
from prefill.models import AIResponse
from prefill.parsing import ParseError, parse_solution
from prefill.prompt_builder import PromptInputError, build_prompt
from prefill.services import import_manual_response

WakeWorker = Callable[[], bool]


class ActionError(Exception):
    """The action doesn't apply to this item right now. str(exc) says why."""


# --- finding what is waiting ---


def get_item(item_id: int) -> PipelineItem:
    return PipelineItem.objects.select_related("problem").get(pk=item_id)


def check_item_is_open(item: PipelineItem) -> None:
    if item.status in (PipelineItem.Status.CANCELLED,):
        raise ActionError(f"the item is {item.status}")


def check_item_waits_for(item: PipelineItem, kind: str) -> None:
    if item.status != PipelineItem.Status.NEEDS_INPUT or item.input_kind != kind:
        raise ActionError(
            f"{item.problem.slug} is not waiting for {kind.replace('_', ' ')}"
        )


def waiting_stage(item: PipelineItem, kind: str) -> ItemStage:
    """The stage this item is parked at, after checking it really waits for `kind`."""
    check_item_is_open(item)
    check_item_waits_for(item, kind)
    return item.stages.get(stage=item.stage, status=ItemStage.Status.WAITING)


# --- moving on once you have answered ---


def finish_waiting_stage(item: PipelineItem, row: ItemStage, detail: str) -> None:
    """Mark the stage done and send the item on to its next stage (or finish it)."""
    outcome = done(detail)
    complete_stage(row, outcome)
    save_fields(item, input_kind="")
    advance_item(item, outcome)


# --- approving reviews ---


def approve_without_waking(item: PipelineItem) -> None:
    row = waiting_stage(item, PipelineItem.InputKind.REVIEW)
    finish_waiting_stage(item, row, "approved")


def approve_review(
    item_id: int, wake: WakeWorker = ensure_worker_running
) -> PipelineItem:
    """Approve one AI note, so the item goes on to save its notes."""
    item = get_item(item_id)
    approve_without_waking(item)
    if wake:
        wake()
    return item


def items_waiting_for_review(
    item_ids: Sequence[int] | None = None,
) -> list[PipelineItem]:
    qs = PipelineItem.objects.select_related("problem").filter(
        status=PipelineItem.Status.NEEDS_INPUT,
        input_kind=PipelineItem.InputKind.REVIEW,
    )
    if item_ids is not None:
        qs = qs.filter(pk__in=item_ids)
    return list(qs)


def approve_all_reviews(
    task_id: Any = None,
    item_ids: Sequence[int] | None = None,
    wake: WakeWorker = ensure_worker_running,
) -> int:
    """Approve every AI note waiting for review. Returns how many were approved."""
    items = items_waiting_for_review(item_ids=item_ids)
    for item in items:
        approve_without_waking(item)
    if items and wake:
        wake()
    return len(items)


# --- submitting a pasted reply ---


def check_reply_is_valid(text: str) -> None:
    try:
        parse_solution(text)
    except ParseError as exc:
        raise ActionError(f"the reply isn't valid:\n{exc}") from exc


def build_prompt_for(item: PipelineItem):
    try:
        return build_prompt(item.problem)
    except PromptInputError as exc:
        raise ActionError(f"can't build the prompt: {exc}") from exc


def save_pasted_reply(
    item: PipelineItem, row: ItemStage, text: str, label: str
) -> None:
    """Store the reply as the active AI note and finish the waiting stage, together."""
    prompt = build_prompt_for(item)
    with transaction.atomic():
        import_manual_response(item.problem, text, label=label, prompt=prompt)
        finish_waiting_stage(item, row, f"manual reply ({label or 'manual'})")


def submit_manual_reply(
    item_id: int,
    text: str,
    label: str = "",
    wake: WakeWorker = ensure_worker_running,
) -> PipelineItem:
    """Use a reply pasted from an AI chat as this item's AI note, then carry on."""
    item = get_item(item_id)
    row = waiting_stage(item, PipelineItem.InputKind.MANUAL_PREFILL)
    check_reply_is_valid(text)
    save_pasted_reply(item, row, text, label)
    if wake:
        wake()
    return item


def select_existing_prefill(
    item_id: int,
    response_id: int,
    wake: WakeWorker = ensure_worker_running,
) -> PipelineItem:
    """Use an existing AIResponse as this item's AI note, then advance the pipeline."""
    item = get_item(item_id)
    row = waiting_stage(item, PipelineItem.InputKind.MANUAL_PREFILL)
    with transaction.atomic():
        chosen = AIResponse.objects.get(
            pk=response_id, problem=item.problem, status=AIResponse.Status.VALID
        )
        AIResponse.objects.filter(problem=item.problem, is_active=True).update(
            is_active=False
        )
        chosen.is_active = True
        chosen.save(update_fields=["is_active"])
        label = chosen.model_name or chosen.provider_name or "existing"
        finish_waiting_stage(item, row, f"reused existing draft ({label})")
    if wake:
        wake()
    return item


# --- putting stages back ---


def resolve_submission_choice(
    item_id: int,
    choice: str,
    wake: WakeWorker = ensure_worker_running,
) -> PipelineItem:
    """Resolve submission choice between existing and newer LeetCode submission.

    choice is either 'keep' or 'use_latest'.
    """
    item = get_item(item_id)
    row = waiting_stage(item, PipelineItem.InputKind.SUBMISSION_CHOICE)
    problem = item.problem
    sub = getattr(problem, "submission", None)

    if choice == "use_latest":
        if sub and sub.newer_submission_id:
            from leetcode.bootstrap import get_client

            client = get_client()
            detail = client.get_submission_details(sub.newer_submission_id)
            if detail:
                sub.submission_id = sub.newer_submission_id
                sub.lang = detail.lang
                sub.code = detail.code
                if detail.submission_date:
                    sub.submission_date = detail.submission_date
                elif sub.newer_submission_date:
                    sub.submission_date = sub.newer_submission_date
        if sub:
            sub.is_stale = False
            sub.newer_submission_id = None
            sub.newer_submission_date = None
            sub.save()
        finish_waiting_stage(item, row, "Updated to newer submission")
    else:  # keep
        if sub:
            sub.is_stale = False
            sub.newer_submission_id = None
            sub.newer_submission_date = None
            sub.save()
        finish_waiting_stage(item, row, "Kept existing submission")

    if wake:
        wake()
    return item


def switch_provider_and_generate(
    item_id: int,
    provider: str,
    model: str = "",
    effort: str = "",
    wake: WakeWorker = ensure_worker_running,
) -> PipelineItem:
    """Update settings to run prefill with a specific AI provider and queue generation."""
    item = get_item(item_id)
    check_item_is_open(item)
    row = item.stages.get(stage=Stage.GENERATE_PREFILL)
    settings = dict(item.settings)
    settings["provider"] = provider
    if model:
        settings["model"] = model
    if effort:
        settings["effort"] = effort
    reset_stage_row(row)
    save_fields(item, settings=settings)
    queue_item_at(item, Stage.GENERATE_PREFILL)
    if wake:
        wake()
    return item


def delete_ai_response(item_id: int, response_id: int) -> None:
    """Delete an AI response candidate. If active, promote another valid response."""
    item = get_item(item_id)
    resp = AIResponse.objects.filter(problem=item.problem, pk=response_id).first()
    if resp:
        was_active = resp.is_active
        resp.delete()
        if was_active:
            next_resp = (
                AIResponse.objects.filter(
                    problem=item.problem, status=AIResponse.Status.VALID
                )
                .order_by("-created_at")
                .first()
            )
            if next_resp:
                next_resp.is_active = True
                next_resp.save(update_fields=["is_active"])


def reset_stage_row(row: ItemStage) -> None:
    """Make a stage as if it had never run."""
    save_fields(
        row,
        status=ItemStage.Status.PENDING,
        detail="",
        error="",
        started_at=None,
        finished_at=None,
    )


def queue_item_at(item: PipelineItem, stage: str) -> None:
    save_fields(
        item,
        status=PipelineItem.Status.QUEUED,
        stage=stage,
        input_kind="",
        error="",
        finished_at=None,
    )


# --- rejecting a review ---


def reset_prefill_and_review(item: PipelineItem) -> None:
    for stage in (Stage.GENERATE_PREFILL, Stage.REVIEW):
        reset_stage_row(item.stages.get(stage=stage))


def reject_review(
    item_id: int, wake: WakeWorker = ensure_worker_running
) -> PipelineItem:
    """Throw the AI note away for now: the item goes back to generating a new one with manual tab preferred."""
    item = get_item(item_id)
    waiting_stage(item, PipelineItem.InputKind.REVIEW)
    reset_prefill_and_review(item)

    # Set preferred_prefill_tab to "manual" in settings so the studio defaults to manual tab
    settings = dict(item.settings)
    settings["preferred_prefill_tab"] = "manual"
    save_fields(item, settings=settings)

    queue_item_at(item, Stage.GENERATE_PREFILL)
    if wake:
        wake()
    return item


# --- retrying a failed item ---


def check_item_can_retry(item: PipelineItem) -> None:
    if item.status != PipelineItem.Status.FAILED:
        raise ActionError(f"{item.problem.slug} has not failed")


def failed_stage(item: PipelineItem) -> ItemStage:
    return item.stages.filter(status=ItemStage.Status.FAILED).order_by("order").first()


def retry_failed_item(
    item_id: int, wake: WakeWorker = ensure_worker_running
) -> PipelineItem:
    """Run the stage that failed again, and carry on from there."""
    item = get_item(item_id)
    check_item_can_retry(item)
    row = failed_stage(item)
    reset_stage_row(row)
    queue_item_at(item, row.stage)
    if wake:
        wake()
    return item
