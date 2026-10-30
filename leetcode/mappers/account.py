from typing import Any

from ..dto import AccountStatusDTO
from .base import build


def map_account_status(raw: dict[str, Any]) -> AccountStatusDTO:
    return build(
        AccountStatusDTO,
        signed_in=bool(raw.get("isSignedIn")),
        username=raw.get("username") or "",
        is_premium=bool(raw.get("isPremium")),
    )
