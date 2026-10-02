"""Read-only, account-specific estimate for an initial Recruiter message."""

from datetime import datetime, timezone
from typing import Any
import re
from urllib.parse import quote

from .unipile_client import get_linkedin_profile_field

NATIVE_COST_QUERY = "talentRecipientInMailCostInfo.bf22ac5feb7f84870e5dfe297a2c1033"


def native_request(project_id: str, contract_id: str, recipients: list[str]) -> dict[str, Any]:
    """Build the observed Recruiter composer GET; it never creates a message."""
    if not re.fullmatch(r"[0-9]+", project_id) or not re.fullmatch(r"[0-9]+", contract_id):
        raise ValueError("Native project and contract IDs must be numeric")
    if not 1 <= len(recipients) <= 25 or len(set(recipients)) != len(recipients):
        raise ValueError("Use 1–25 unique Recruiter recipient IDs")
    if any(not re.fullmatch(r"AE[A-Za-z0-9_-]+", identifier) for identifier in recipients):
        raise ValueError("Native cost quotes require canonical Recruiter IDs")
    project = quote(f"urn:li:ts_hiring_project:(urn:li:ts_contract:{contract_id},{project_id})", safe="")
    people = ",".join(quote(f"urn:li:ts_profile:{identifier}", safe="") for identifier in recipients)
    return {"method": "GET", "url": "https://www.linkedin.com/talent/api/graphql",
            "bypass_url_encoding": True, "query_params": {
                "includeWebMetadata": "true", "queryId": NATIVE_COST_QUERY,
                "variables": f"(hiringProjectUrn:{project},recipients:List({people}),withMessageSendEligibility:true)",
            }}


def parse_native(response: dict[str, Any], recipients: list[str]) -> dict[str, dict[str, Any]]:
    """Project the denormalized v2 response and require an exact recipient join."""
    data = response.get("data", {}).get("data", {})
    collection = data.get("recipientInMailCostInfoByRecipients", {})
    elements = collection.get("elements")
    if not isinstance(elements, list):
        raise ValueError("Native Recruiter cost response is missing elements")
    results = {}
    for item in elements:
        if not isinstance(item, dict) or not isinstance(item.get("recipient"), dict):
            raise ValueError("Native cost quote has no recipient identity")
        urn = item["recipient"].get("entityUrn")
        identifier = urn.removeprefix("urn:li:ts_profile:") if isinstance(urn, str) else None
        if identifier not in recipients or identifier in results or urn != f"urn:li:ts_profile:{identifier}":
            raise ValueError("Native cost response has an unexpected or duplicate recipient")
        if item.get("entityUrn") != f"urn:li:ts_recipient_inmail_cost_info:{urn}":
            raise ValueError("Native cost entity and recipient do not match")
        cost, allowed = item.get("inMailCost"), item.get("canAcceptInMails")
        cost = cost if type(cost) is int and cost in (0, 1) else None
        allowed = allowed if type(allowed) is bool else None
        status = "unavailable" if allowed is False else (
            "free" if allowed is True and cost == 0 else
            "requires_credit" if allowed is True and cost == 1 else "unknown")
        eligibility = item.get("messageSendEligibility", {})
        eligibility = eligibility if isinstance(eligibility, dict) else {}
        email = eligibility.get("emailInitialSendEligibility", {})
        email = email if isinstance(email, dict) else {}
        results[identifier] = {
            "operation": "messaging-cost", "source": "native_recruiter_composer",
            "checked_at": datetime.now(timezone.utc).isoformat(), "status": status,
            "expected_inmail_credits": cost if status in {"free", "requires_credit"} else None,
            "quoted_inmail_credits": cost, "can_accept_inmails": allowed,
            "email_initial_send_eligible": email.get("eligible") if type(email.get("eligible")) is bool else None,
            "reason": "Recruiter composer reports the recipient's credit cost and InMail eligibility.",
            "limitations": ["Read-only quote, not a send guarantee or monetary price; no message was sent.",
                            "Applies to the selected Recruiter contract and project at the checked time."],
        }
    if set(results) != set(recipients):
        raise ValueError("Native cost response did not return every requested recipient")
    return results


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
