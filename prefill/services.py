import time

import structlog
from django.db import transaction

from problems.models import Problem

from .models import AIResponse
from .parsing import ParseError, parse_solution
from .prompt_builder import Prompt, build_prompt
from .providers.base import Provider

logger = structlog.get_logger(__name__)


def activate(response: AIResponse) -> None:
    """Make this the response the notes app uses; the old active one is kept."""
    if response.status != AIResponse.Status.VALID:
        raise ValueError("only a valid response can be activated")
    with transaction.atomic():
        AIResponse.objects.filter(problem=response.problem, is_active=True).exclude(
            pk=response.pk
        ).update(is_active=False)
        if not response.is_active:
            response.is_active = True
            response.save(update_fields=["is_active"])


def save_response(
    problem: Problem,
    raw_text: str,
    *,
    source: str,
    provider_name: str = "",
    model_name: str = "",
    prompt: Prompt | None = None,
) -> AIResponse:
    """Parse, store, and (if valid) activate a response. The one path for all sources."""
    prompt = prompt or build_prompt(problem)
    try:
        parsed, error = parse_solution(raw_text).model_dump(), ""
    except ParseError as exc:
        parsed, error = None, str(exc)

    response = AIResponse.objects.create(
        problem=problem,
        source=source,
        provider_name=provider_name,
        model_name=model_name,
        prompt_version=prompt.version,
        input_hash=prompt.input_hash,
        raw_text=raw_text,
        parsed=parsed,
        status=AIResponse.Status.VALID if parsed else AIResponse.Status.INVALID,
        error=error,
    )
    if parsed:
        activate(response)
    return response


def import_manual_response(
    problem: Problem, raw_text: str, *, label: str = "", prompt: Prompt | None = None
) -> AIResponse:
    return save_response(
        problem,
        raw_text,
        source=AIResponse.Source.MANUAL,
        provider_name=label or "manual",
        prompt=prompt,
    )


def generate_for_problem(problem: Problem, provider: Provider) -> AIResponse:
    """Ask a provider for the note and store the result.

    Raises PromptInputError (problem not ready) or ProviderError (the call
    failed). Output that doesn't validate is stored as an invalid response.
    """
    prompt = build_prompt(problem)
    started = time.monotonic()
    result = provider.generate(prompt.system, prompt.user)
    response = save_response(
        problem,
        result.text,
        source=AIResponse.Source.PROVIDER,
        provider_name=provider.name,
        model_name=result.model or provider.model,
        prompt=prompt,
    )
    logger.info(
        "ai_note_generated",
        slug=problem.slug,
        provider=provider.name,
        status=response.status,
        seconds=round(time.monotonic() - started, 1),
    )
    return response
