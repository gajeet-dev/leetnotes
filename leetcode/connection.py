"""Is the saved LeetCode login working? One call that answers in words the UI can show."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from django.utils import timezone

from userconfig import effective

from .bootstrap import get_client
from .client import LeetCodeClient
from .dto import AccountStatusDTO
from .exceptions import (
    AuthError,
    LeetCodeError,
    NotConfigured,
    RateLimitError,
    TransportError,
)

SIGNED_OUT_MESSAGE = (
    "LeetCode doesn't accept these cookies: they have probably expired. "
    "Copy fresh ones from your browser and update them in Settings."
)
RATE_LIMITED_MESSAGE = (
    "LeetCode is rate limiting requests. Wait a few minutes, then test again."
)


class ConnectionState(StrEnum):
    CONNECTED = "connected"
    NOT_CONFIGURED = "not_configured"  # no cookies saved yet
    SIGNED_OUT = "signed_out"  # cookies are set but LeetCode rejects them
    RATE_LIMITED = "rate_limited"
    UNREACHABLE = "unreachable"  # network problem, or LeetCode is down
    UNEXPECTED = "unexpected"  # LeetCode answered in a way we don't understand


@dataclass(frozen=True)
class ConnectionResult:
    state: ConnectionState
    message: str
    username: str = ""
    is_premium: bool = False
    username_differs: bool = (
        False  # the saved username isn't the account the cookies belong to
    )
    checked_at: datetime = field(default_factory=timezone.now)

    @property
    def ok(self) -> bool:
        return self.state is ConnectionState.CONNECTED


def same_username(first: str, second: str) -> bool:
    return first.strip().lower() == second.strip().lower()


def connected_message(account: AccountStatusDTO, differs: bool) -> str:
    message = f"Connected as {account.username}."
    if differs:
        message += (
            f" Your saved username is {effective.leetcode_username()!r}: "
            "update it in Settings if that's wrong."
        )
    return message


def result_for_account(account: AccountStatusDTO) -> ConnectionResult:
    """Turn LeetCode's answer into a result."""
    if not account.signed_in:
        return ConnectionResult(ConnectionState.SIGNED_OUT, SIGNED_OUT_MESSAGE)
    saved_username = effective.leetcode_username()
    differs = bool(saved_username) and not same_username(
        saved_username, account.username
    )
    return ConnectionResult(
        ConnectionState.CONNECTED,
        connected_message(account, differs),
        username=account.username,
        is_premium=account.is_premium,
        username_differs=differs,
    )


def result_for_error(error: LeetCodeError) -> ConnectionResult:
    """Turn a failure into a result. The more specific errors are checked first."""
    if isinstance(error, NotConfigured):
        return ConnectionResult(ConnectionState.NOT_CONFIGURED, str(error))
    if isinstance(error, AuthError):
        return ConnectionResult(ConnectionState.SIGNED_OUT, SIGNED_OUT_MESSAGE)
    if isinstance(error, RateLimitError):
        return ConnectionResult(ConnectionState.RATE_LIMITED, RATE_LIMITED_MESSAGE)
    if isinstance(error, TransportError):
        return ConnectionResult(
            ConnectionState.UNREACHABLE, f"Couldn't reach LeetCode: {error}"
        )
    return ConnectionResult(
        ConnectionState.UNEXPECTED, f"Unexpected answer from LeetCode: {error}"
    )


def check_connection(
    client_factory: Callable[[], LeetCodeClient] = get_client,
) -> ConnectionResult:
    """Ask LeetCode who the saved cookies belong to. Never raises: failures become results."""
    try:
        account = client_factory().get_account_status()
    except LeetCodeError as error:
        return result_for_error(error)
    return result_for_account(account)
