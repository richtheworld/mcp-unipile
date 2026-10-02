"""Read-only, account-specific estimate for an initial Recruiter message."""

from datetime import datetime, timezone
from typing import Any

from .unipile_client import get_linkedin_profile_field


def assess(profile: dict[str, Any]) -> dict[str, Any]:
    def boolean(field: str) -> bool | None:
        value = get_linkedin_profile_field(profile, field)
        return value if isinstance(value, bool) else None

    opened = boolean("is_open_profile")
    allowed = boolean("can_send_inmail")
    distance = get_linkedin_profile_field(profile, "network_distance")
    known_distances = {"SELF", "FIRST_DEGREE", "SECOND_DEGREE", "THIRD_DEGREE", "OUT_OF_NETWORK"}
    distance = distance if isinstance(distance, str) and distance in known_distances else None
    cost = None
    status = "unknown"
    reason = "Insufficient profile evidence; missing fields are not false."
    if distance == "SELF":
        status, reason = "unavailable", "The recipient is the sending account."
    elif distance == "FIRST_DEGREE":
        cost, status, reason = 0, "free", "First-degree connection; messaging uses no InMail credit."
    elif allowed is False:
        status, reason = "unavailable", "The profile reports can_send_inmail=false."
    elif opened is True:
        cost, status, reason = 0, "free", "Open Profile in this account's Recruiter context."
    elif opened is False and distance in {"SECOND_DEGREE", "THIRD_DEGREE", "OUT_OF_NETWORK"} and allowed is True:
        cost, status, reason = 1, "requires_credit", "Reachable non-connection without Open Profile."
    return {
        "operation": "messaging-cost",
        "profile_variant": "linkedin_recruiter",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "expected_inmail_credits": cost,
        "reason": reason,
        "signals": {"is_open_profile": opened, "network_distance": distance,
                    "can_send_inmail": allowed, "is_open_to_work": boolean("is_open_to_work")},
        "scope": "Initial Recruiter contact from this connected account; not an existing-chat reply.",
        "limitations": [
            "An estimate, not a send guarantee or a monetary price; no message was sent.",
            "Open to Work does not establish free InMail eligibility.",
            "Free sends can still fail because of limits or an empty credit balance; balance is not checked here.",
        ],
    }
