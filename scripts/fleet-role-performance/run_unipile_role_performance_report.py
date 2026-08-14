#!/usr/bin/env python3
"""Read-only Fleet AI LinkedIn Recruiter performance report using Unipile v2."""

from __future__ import annotations

import json
import math
import os
from collections import Counter
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests


ROOT = Path(
    os.environ.get(
        "UNIPILE_METRICS_ROOT",
        Path(__file__).resolve().parent,
    )
).expanduser()
HISTORY_PATH = Path(
    os.environ.get(
        "UNIPILE_METRICS_HISTORY_PATH",
        ROOT / "work" / "unipile_role_performance_history.jsonl",
    )
).expanduser()
RESULT_PATH = os.environ.get("UNIPILE_METRICS_RESULT_PATH")
REPORT_TIMEZONE = ZoneInfo("Europe/London")
V2_BASE_URL = "https://api.unipile.com"
FLEET_PREFIX = "Fleet AI - "
MIGRATION_MARKER = "Dynamism migration 20260804"
EXPECTED_ACTIVE_ROLES = 9
INVENTORY_PATH = Path(
    os.environ.get(
        "UNIPILE_METRICS_INVENTORY_PATH",
        ROOT / "work" / "unipile_fleet_role_inventory_v2.json",
    )
).expanduser()
INVENTORY_MAX_AGE_HOURS = float(
    os.environ.get("UNIPILE_METRICS_INVENTORY_MAX_AGE_HOURS", "168")
)
LAST_INVENTORY_METADATA: dict[str, Any] = {}


def numeric_or_none(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return value
    return None


def snapshot_datetime(snapshot: dict[str, Any]) -> datetime:
    value = snapshot.get("captured_at")
    if not isinstance(value, str):
        raise RuntimeError("Successful history snapshot is missing captured_at.")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_successful_snapshots() -> list[dict[str, Any]]:
    if not HISTORY_PATH.exists():
        return []
    snapshots: list[dict[str, Any]] = []
    for line_number, line in enumerate(HISTORY_PATH.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Invalid history JSON on line {line_number}: {exc}"
            ) from exc
        if candidate.get("success") is True and candidate.get("roles"):
            snapshots.append(candidate)
    return sorted(snapshots, key=snapshot_datetime)


def sum_if_complete(roles: list[dict[str, Any]], field: str) -> int | float | None:
    values = [role[field] for role in roles]
    if any(value is None for value in values):
        return None
    return sum(values)


def conversion_pct(applicants: Any, views: Any) -> float | None:
    applicants_value = numeric_or_none(applicants)
    views_value = numeric_or_none(views)
    if applicants_value is None or views_value is None or views_value <= 0:
        return None
    return round(applicants_value / views_value * 100, 2)


def difference(current: Any, previous: Any) -> int | float | None:
    current_value = numeric_or_none(current)
    previous_value = numeric_or_none(previous)
    if current_value is None or previous_value is None:
        return None
    return round(current_value - previous_value, 2)


def period_boundaries(captured_at: datetime) -> dict[str, datetime]:
    local_capture = captured_at.astimezone(REPORT_TIMEZONE)
    local_day = local_capture.date()
    week_start_date = local_day - timedelta(days=local_day.weekday())
    month_start_date = local_day.replace(day=1)
    return {
        "trailing_24_hours": captured_at - timedelta(hours=24),
        "week_to_date": datetime.combine(
            week_start_date, time.min, REPORT_TIMEZONE
        ).astimezone(timezone.utc),
        "month_to_date": datetime.combine(
            month_start_date, time.min, REPORT_TIMEZONE
        ).astimezone(timezone.utc),
    }


def select_period_baseline(
    snapshots: list[dict[str, Any]], target_start: datetime
) -> tuple[dict[str, Any] | None, bool]:
    if not snapshots:
        return None, True
    eligible = [
        snapshot
        for snapshot in snapshots
        if snapshot_datetime(snapshot) <= target_start
    ]
    if eligible:
        return eligible[-1], False
    return snapshots[0], True


def build_period_summary(
    name: str,
    target_start: datetime,
    current_roles: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
) -> dict[str, Any]:
    baseline, partial = select_period_baseline(snapshots, target_start)
    if baseline is None:
        return {
            "name": name,
            "available": False,
            "target_start": target_start.isoformat(),
            "baseline_captured_at": None,
            "baseline_offset_hours": None,
            "approximate_boundary": True,
            "partial": True,
            "role_deltas": [],
            "warnings": ["No successful historical snapshot is available."],
        }

    baseline_roles = {
        str(role["job_id"]): role
        for role in baseline.get("roles", [])
        if role.get("job_id")
    }
    role_deltas: list[dict[str, Any]] = []
    for current in current_roles:
        prior = baseline_roles.get(str(current["job_id"]))
        role_deltas.append(
            {
                "project_name": current.get("project_name"),
                "public_title": current.get("public_title"),
                "job_id": current["job_id"],
                "views": difference(current.get("views"), prior.get("views"))
                if prior
                else None,
                "applicants": difference(
                    current.get("applicants"), prior.get("applicants")
                )
                if prior
                else None,
            }
        )

    for role in role_deltas:
        role["conversion_pct"] = conversion_pct(role["applicants"], role["views"])

    current_ids = {str(role["job_id"]) for role in current_roles}
    baseline_ids = set(baseline_roles)
    baseline_time = snapshot_datetime(baseline)
    baseline_offset_hours = round(
        (baseline_time - target_start).total_seconds() / 3600, 2
    )
    warnings: list[str] = []
    if partial:
        warnings.append(
            "History starts after the requested period boundary; results cover only "
            "the available baseline interval."
        )
    elif baseline_offset_hours < 0:
        warnings.append(
            "The latest baseline at or before the requested boundary predates it by "
            f"{abs(baseline_offset_hours):g} hours; metrics cover that additional time."
        )
    missing_current = sorted(current_ids - baseline_ids)
    removed_from_scope = sorted(baseline_ids - current_ids)
    if missing_current:
        warnings.append(
            "Current Fleet roles missing from the selected baseline; aggregate deltas "
            f"are unavailable for job IDs: {missing_current}"
        )
    if removed_from_scope:
        warnings.append(
            "The baseline contained roles outside the current nine-role Fleet scope; "
            "their counters are excluded from this period."
        )

    total_views = sum_if_complete(role_deltas, "views")
    total_applicants = sum_if_complete(role_deltas, "applicants")
    negative_counters = [
        str(role["job_id"])
        for role in role_deltas
        if any(
            numeric_or_none(role[field]) is not None and role[field] < 0
            for field in ("views", "applicants")
        )
    ]
    if negative_counters:
        warnings.append(
            f"Cumulative counters decreased for job IDs: {negative_counters}"
        )

    top_roles = sorted(
        [
            role
            for role in role_deltas
            if numeric_or_none(role["applicants"]) is not None
            and role["applicants"] > 0
        ],
        key=lambda role: (-role["applicants"], role.get("public_title") or ""),
    )[:3]

    return {
        "name": name,
        "available": True,
        "target_start": target_start.isoformat(),
        "baseline_captured_at": baseline.get("captured_at"),
        "baseline_offset_hours": baseline_offset_hours,
        "approximate_boundary": baseline_offset_hours != 0,
        "partial": partial,
        "views": total_views,
        "applicants": total_applicants,
        "conversion_pct": conversion_pct(total_applicants, total_views),
        "new_job_ids": missing_current,
        "closed_job_ids": removed_from_scope,
        "top_three_by_applicants": [
            {
                "project_name": role["project_name"],
                "public_title": role["public_title"],
                "job_id": role["job_id"],
                "applicants": role["applicants"],
            }
            for role in top_roles
        ],
        "role_deltas": role_deltas,
        "warnings": warnings,
    }


def v2_session() -> requests.Session:
    api_key = os.environ.get("UNIPILE_V2_API_KEY")
    if not api_key:
        raise RuntimeError(
            "UNIPILE_V2_API_KEY is required; V1 and legacy credentials are rejected."
        )
    configured_base = os.environ.get("UNIPILE_V2_BASE_URL", V2_BASE_URL).rstrip("/")
    if configured_base != V2_BASE_URL:
        raise RuntimeError("Unipile v2 base URL must be https://api.unipile.com")
    session = requests.Session()
    session.headers.update({"X-API-KEY": api_key, "accept": "application/json"})
    return session


def api_get(
    session: requests.Session, path: str, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    response = session.get(
        f"{V2_BASE_URL}{path}", params=params or {}, timeout=60
    )
    if not response.ok:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        error_type = payload.get("type") or "unipile_api_error"
        detail = payload.get("detail") or payload.get("title") or response.reason
        raise RuntimeError(f"Unipile v2 {response.status_code} {error_type}: {detail}")
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError(f"Unipile v2 returned a non-object response for {path}")
    return data


def _job_payload(payload: dict[str, Any], job_id: str) -> dict[str, Any]:
    candidate = payload.get("data")
    if isinstance(candidate, dict):
        return candidate
    if isinstance(candidate, list) and len(candidate) == 1 and isinstance(candidate[0], dict):
        return candidate[0]
    return payload


def _inventory_roles_from_snapshot(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    roles = []
    for role in snapshot.get("roles", []):
        if not isinstance(role, dict):
            continue
        required = ("project_id", "job_id", "project_name", "public_title")
        if all(role.get(field) for field in required):
            roles.append(
                {
                    "project_id": str(role["project_id"]),
                    "job_id": str(role["job_id"]),
                    "project_name": role["project_name"],
                    "public_title": role["public_title"],
                }
            )
    return roles


def _write_inventory(captured_at: str, roles: list[dict[str, Any]], source: str) -> None:
    INVENTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = INVENTORY_PATH.with_suffix(INVENTORY_PATH.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            {
                "api_version": "v2",
                "captured_at": captured_at,
                "source": source,
                "expected_active_roles": EXPECTED_ACTIVE_ROLES,
                "roles": roles,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    temporary.replace(INVENTORY_PATH)


def _load_fresh_inventory(snapshots: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], float, str] | None:
    inventory: dict[str, Any] | None = None
    source = "inventory_file"
    if INVENTORY_PATH.exists():
        try:
            candidate = json.loads(INVENTORY_PATH.read_text())
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid V2 Fleet inventory JSON: {exc}") from exc
        if isinstance(candidate, dict):
            inventory = candidate
    elif snapshots:
        latest = snapshots[-1]
        seeded_roles = _inventory_roles_from_snapshot(latest)
        if len(seeded_roles) == EXPECTED_ACTIVE_ROLES:
            _write_inventory(latest["captured_at"], seeded_roles, "successful_v2_snapshot_seed")
            inventory = {
                "captured_at": latest["captured_at"],
                "roles": seeded_roles,
            }
            source = "successful_v2_snapshot_seed"

    if not inventory:
        return None
    captured_at = inventory.get("captured_at")
    roles = inventory.get("roles")
    if not isinstance(captured_at, str) or not isinstance(roles, list):
        raise RuntimeError("V2 Fleet inventory is missing captured_at or roles.")
    parsed = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    age_hours = (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() / 3600
    normalized = _inventory_roles_from_snapshot({"roles": roles})
    if len(normalized) != EXPECTED_ACTIVE_ROLES:
        raise RuntimeError(
            f"V2 Fleet inventory must contain exactly {EXPECTED_ACTIVE_ROLES} roles; found {len(normalized)}."
        )
    if age_hours < 0 or age_hours > INVENTORY_MAX_AGE_HOURS:
        return None
    return normalized, round(age_hours, 2), source


def _load_cached_fleet_roles(
    session: requests.Session,
    account_id: str,
    inventory_roles: list[dict[str, Any]],
    inventory_age_hours: float,
    inventory_source: str,
) -> tuple[list[dict[str, Any]], int, int, list[str], str]:
    roles: list[dict[str, Any]] = []
    for cached in inventory_roles:
        job_id = str(cached["job_id"])
        payload = api_get(
            session,
            f"/v2/{account_id}/linkedin/recruiter/jobs/{job_id}",
        )
        job = _job_payload(payload, job_id)
        returned_id = str(job.get("id") or job_id)
        if returned_id != job_id:
            raise RuntimeError(f"V2 job response ID mismatch for cached Fleet role {job_id}.")
        if str(job.get("state") or "").upper() != "LISTED":
            raise RuntimeError(f"Cached Fleet role {job_id} is no longer LISTED; refresh inventory required.")
        project_id = str(job.get("project_id") or cached["project_id"])
        if project_id != str(cached["project_id"]):
            raise RuntimeError(f"V2 project mapping changed for cached Fleet role {job_id}; refresh inventory required.")
        views = numeric_or_none(job.get("views_count"))
        applicants = numeric_or_none(job.get("applications_count"))
        roles.append(
            {
                "project_name": cached["project_name"],
                "public_title": job.get("title") or cached["public_title"],
                "job_id": job_id,
                "project_id": project_id,
                "state": "active",
                "provider_state": job.get("state"),
                "views": views,
                "applicants": applicants,
                "conversion_pct": conversion_pct(applicants, views),
            }
        )
    roles.sort(key=lambda role: (str(role.get("public_title") or ""), role["job_id"]))
    LAST_INVENTORY_METADATA.update(
        {
            "mode": "cached",
            "source": inventory_source,
            "captured_at": inventory_roles and INVENTORY_PATH.exists() and json.loads(INVENTORY_PATH.read_text()).get("captured_at"),
            "age_hours": inventory_age_hours,
            "project_records_scanned": 0,
            "job_records_scanned": len(roles),
        }
    )
    return roles, 0, len(roles), [], "cached"


def resolve_account_id(session: requests.Session) -> str:
    configured = os.environ.get("UNIPILE_V2_LINKEDIN_ACCOUNT_ID")
    if configured:
        if not configured.startswith("acc_"):
            raise RuntimeError("Configured Unipile v2 account ID must start with acc_")
        return configured
    payload = api_get(
        session, "/v2/accounts/", {"provider": "linkedin", "limit": 100}
    )
    items = payload.get("items") or payload.get("data") or []
    healthy = [
        item
        for item in items
        if str(item.get("provider") or item.get("type") or "").lower() == "linkedin"
        and str(item.get("status") or "").lower() == "running"
        and str(
            ((item.get("metadata") or {}).get("products_connection_status") or {}).get(
                "recruiter", ""
            )
        ).lower()
        == "running"
    ]
    if len(healthy) != 1:
        raise RuntimeError(
            "Expected exactly one running V2 LinkedIn Recruiter account; "
            f"found {len(healthy)}."
        )
    return str(healthy[0]["id"])


def paginated_get(
    session: requests.Session, path: str, *, page_size: int = 25
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    for _ in range(100):
        payload = api_get(session, path, {"limit": page_size, "offset": offset})
        page = payload.get("data") or payload.get("items") or []
        if not isinstance(page, list):
            raise RuntimeError(f"Unipile v2 collection response is invalid for {path}")
        rows.extend(item for item in page if isinstance(item, dict))
        total = payload.get("total_count")
        if len(page) < page_size or (
            isinstance(total, int) and len(rows) >= total
        ):
            return rows
        offset += len(page)
    raise RuntimeError(f"Unipile v2 pagination exceeded 100 pages for {path}")


def has_active_job_channel(project: dict[str, Any]) -> bool:
    channels = ((project.get("talent_pool") or {}).get("channels") or [])
    return any(
        channel.get("type") == "JOB_POSTING"
        and str(channel.get("state") or "").upper() == "ACTIVE"
        for channel in channels
    )


def load_fleet_roles(
    session: requests.Session, account_id: str
) -> tuple[list[dict[str, Any]], int, int, list[str], str]:
    snapshots = load_successful_snapshots()
    cached_inventory = _load_fresh_inventory(snapshots)
    if cached_inventory is not None:
        inventory_roles, inventory_age_hours, inventory_source = cached_inventory
        return _load_cached_fleet_roles(
            session,
            account_id,
            inventory_roles,
            inventory_age_hours,
            inventory_source,
        )

    projects = paginated_get(
        session, f"/v2/{account_id}/linkedin/recruiter/projects"
    )
    jobs = paginated_get(session, f"/v2/{account_id}/linkedin/recruiter/jobs")
    fleet_projects = {
        str(project["id"]): project
        for project in projects
        if not project.get("archived")
        and str(project.get("name") or "").startswith(FLEET_PREFIX)
        and MIGRATION_MARKER in str(project.get("name") or "")
    }
    selected_jobs = [
        job
        for job in jobs
        if str(job.get("project_id")) in fleet_projects
        and str(job.get("state") or "").upper() == "LISTED"
        and has_active_job_channel(fleet_projects[str(job.get("project_id"))])
    ]
    duplicate_job_ids = sorted(
        job_id
        for job_id, count in Counter(str(job.get("id")) for job in selected_jobs).items()
        if count > 1
    )
    if duplicate_job_ids:
        raise RuntimeError(f"Duplicate listed Fleet job IDs: {duplicate_job_ids}")
    if len(selected_jobs) != EXPECTED_ACTIVE_ROLES:
        raise RuntimeError(
            f"Expected {EXPECTED_ACTIVE_ROLES} listed Fleet AI roles; "
            f"found {len(selected_jobs)}. Refusing to publish a partial report."
        )

    roles: list[dict[str, Any]] = []
    for job in selected_jobs:
        project = fleet_projects[str(job["project_id"])]
        views = numeric_or_none(job.get("views_count"))
        applicants = numeric_or_none(job.get("applications_count"))
        roles.append(
            {
                "project_name": project.get("name"),
                "public_title": job.get("title")
                or (project.get("metadata") or {}).get("job_title"),
                "job_id": str(job["id"]),
                "project_id": str(project["id"]),
                "state": "active",
                "provider_state": job.get("state"),
                "views": views,
                "applicants": applicants,
                "conversion_pct": conversion_pct(applicants, views),
            }
        )
    roles.sort(key=lambda role: (str(role.get("public_title") or ""), role["job_id"]))
    inventory_roles = [
        {
            "project_id": role["project_id"],
            "job_id": role["job_id"],
            "project_name": role["project_name"],
            "public_title": role["public_title"],
        }
        for role in roles
    ]
    captured_at = datetime.now(timezone.utc).isoformat()
    _write_inventory(captured_at, inventory_roles, "full_v2_inventory_refresh")
    LAST_INVENTORY_METADATA.update(
        {
            "mode": "refreshed",
            "source": "full_v2_inventory_refresh",
            "captured_at": captured_at,
            "age_hours": 0,
            "project_records_scanned": len(projects),
            "job_records_scanned": len(jobs),
        }
    )
    closed_matching = sorted(
        str(job["id"])
        for job in jobs
        if str(job.get("project_id")) in fleet_projects
        and str(job.get("state") or "").upper() != "LISTED"
    )
    return roles, len(projects), len(jobs), closed_matching, "refreshed"


def compact_title(title: str | None) -> str:
    mapping = {
        "Forward Deployed Engineer": "Fwd Deployed Eng",
        "Forward Deployed Software Engineer": "Fwd Deployed SWE",
        "Senior Forward Deployed Software Engineer": "Senior Fwd Deployed SWE",
        "Member of Technical Staff, Deployments": "MTS Deployments",
        "Senior Software Engineer": "Senior SWE",
        "Staff Software Engineer": "Staff SWE",
    }
    return mapping.get(str(title), str(title or "Unknown role"))


def fmt_int(value: Any) -> str:
    number = numeric_or_none(value)
    return "—" if number is None else f"{int(number):,}"


def fmt_pct(value: Any) -> str:
    number = numeric_or_none(value)
    return "—" if number is None else f"{number:.2f}%"


def role_period_value(period: dict[str, Any], job_id: str) -> Any:
    for role in period.get("role_deltas", []):
        if str(role.get("job_id")) == str(job_id):
            return role.get("applicants")
    return None


def slack_report(snapshot: dict[str, Any]) -> str:
    captured = snapshot_datetime(snapshot).astimezone(REPORT_TIMEZONE)
    totals = snapshot["totals"]
    periods = snapshot["periods"]
    period_rows = [
        ("24H", periods["trailing_24_hours"]),
        ("WTD", periods["week_to_date"]),
        ("MTD", periods["month_to_date"]),
    ]
    window_lines = [f"{'WINDOW':<8}{'VIEWS':>10}{'APPS':>9}{'CVR':>10}"]
    for label, period in period_rows:
        window_lines.append(
            f"{label:<8}{fmt_int(period.get('views')):>10}"
            f"{fmt_int(period.get('applicants')):>9}"
            f"{fmt_pct(period.get('conversion_pct')):>10}"
        )

    role_lines = [f"{'ROLE':<25}{'TOTAL':>8}{'24H':>7}{'WTD':>7}{'MTD':>7}"]
    for role in snapshot["roles"]:
        title = compact_title(role.get("public_title"))[:25]
        values = [
            role.get("applicants"),
            role_period_value(periods["trailing_24_hours"], role["job_id"]),
            role_period_value(periods["week_to_date"], role["job_id"]),
            role_period_value(periods["month_to_date"], role["job_id"]),
        ]
        role_lines.append(
            f"{title:<25}{fmt_int(values[0]):>8}{fmt_int(values[1]):>7}"
            f"{fmt_int(values[2]):>7}{fmt_int(values[3]):>7}"
        )

    inventory_lines = [f"{'ROLE':<25}{'VIEWS':>9}{'APPS':>8}{'CVR':>9}"]
    for role in snapshot["roles"]:
        title = compact_title(role.get("public_title"))[:25]
        inventory_lines.append(
            f"{title:<25}{fmt_int(role.get('views')):>9}"
            f"{fmt_int(role.get('applicants')):>8}"
            f"{fmt_pct(role.get('conversion_pct')):>9}"
        )

    leaders = periods["trailing_24_hours"].get("top_three_by_applicants", [])
    leader_lines = [
        f"{index}. {compact_title(role.get('public_title'))} — "
        f"{fmt_int(role.get('applicants'))} applicants gained"
        for index, role in enumerate(leaders, start=1)
    ] or ["No positive applicant gains are available for the selected baseline."]

    baseline_notes = []
    for label, period in period_rows:
        baseline = period.get("baseline_captured_at")
        if baseline:
            baseline_local = snapshot_datetime({"captured_at": baseline}).astimezone(
                REPORT_TIMEZONE
            )
            note = f"{label} {baseline_local.strftime('%d %b %H:%M')}"
            if period.get("partial"):
                note += " (PARTIAL)"
            baseline_notes.append(note)
    data_note = "Baselines: " + "; ".join(baseline_notes) + "."

    return "\n".join(
        [
            "**Inbound Application Performance**",
            captured.strftime("%A, %d %B %Y · cutoff %H:%M %Z"),
            "",
            "**Portfolio**",
            f"• Active Fleet AI roles: {snapshot['active_count']}",
            f"• Lifetime views: {fmt_int(totals.get('views'))}",
            f"• Lifetime applicants: {fmt_int(totals.get('applicants'))}",
            f"• Cumulative conversion: {fmt_pct(totals.get('portfolio_conversion_pct'))}",
            "",
            "**Performance windows**",
            "```\n" + "\n".join(window_lines) + "\n```",
            "",
            "**Applications by role**",
            "```\n" + "\n".join(role_lines) + "\n```",
            "",
            "**Current role inventory**",
            "```\n" + "\n".join(inventory_lines) + "\n```",
            "",
            "**Leaders**",
            *leader_lines,
            "",
            f"_Data note: {data_note} Source: Unipile v2; nine listed Fleet AI roles only._",
        ]
    )


def write_result(snapshot: dict[str, Any]) -> None:
    if not RESULT_PATH:
        return
    path = Path(RESULT_PATH).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
    temp.replace(path)


def main() -> None:
    snapshots = load_successful_snapshots()
    previous = snapshots[-1] if snapshots else None
    session = v2_session()
    account_id = resolve_account_id(session)
    roles, project_count, job_count, closed_matching, inventory_mode = load_fleet_roles(
        session, account_id
    )

    prior_roles = {
        str(role["job_id"]): role
        for role in (previous or {}).get("roles", [])
        if role.get("job_id")
    }
    for role in roles:
        prior = prior_roles.get(str(role["job_id"]))
        prior_conversion = (
            conversion_pct(prior.get("applicants"), prior.get("views"))
            if prior
            else None
        )
        role["delta_views"] = difference(role.get("views"), prior.get("views")) if prior else None
        role["delta_applicants"] = (
            difference(role.get("applicants"), prior.get("applicants"))
            if prior
            else None
        )
        role["delta_conversion_points"] = (
            difference(role.get("conversion_pct"), prior_conversion) if prior else None
        )

    current_ids = {str(role["job_id"]) for role in roles}
    previous_ids = set(prior_roles)
    total_views = sum_if_complete(roles, "views")
    total_applicants = sum_if_complete(roles, "applicants")
    captured_at_dt = datetime.now(timezone.utc)
    periods = {
        name: build_period_summary(name, boundary, roles, snapshots)
        for name, boundary in period_boundaries(captured_at_dt).items()
    }

    warnings: list[str] = []
    if previous is None:
        warnings.append("First successful baseline; period deltas are unavailable.")
    for field in ("views", "applicants"):
        missing = [role["job_id"] for role in roles if role[field] is None]
        if missing:
            warnings.append(f"{field} unavailable for job IDs: {missing}")
    for name, period in periods.items():
        warnings.extend(f"{name}: {warning}" for warning in period.get("warnings", []))

    snapshot: dict[str, Any] = {
        "success": True,
        "captured_at": captured_at_dt.isoformat(),
        "api_version": "v2",
        "source": "Unipile v2 LinkedIn Recruiter projects and jobs",
        "scope": {
            "project_prefix": FLEET_PREFIX,
            "migration_marker": MIGRATION_MARKER,
            "provider_job_state": "LISTED",
            "expected_active_roles": EXPECTED_ACTIVE_ROLES,
        },
        "project_records_scanned": project_count,
        "job_records_scanned": job_count,
        "inventory": {
            "mode": inventory_mode,
            "captured_at": LAST_INVENTORY_METADATA.get("captured_at"),
            "age_hours": LAST_INVENTORY_METADATA.get("age_hours"),
            "source": LAST_INVENTORY_METADATA.get("source"),
            "refresh_after_hours": INVENTORY_MAX_AGE_HOURS,
        },
        "active_count": len(roles),
        "roles": roles,
        "totals": {
            "views": total_views,
            "applicants": total_applicants,
            "portfolio_conversion_pct": conversion_pct(total_applicants, total_views),
        },
        "periods": periods,
        "new_job_ids": sorted(current_ids - previous_ids) if previous else [],
        "closed_job_ids": sorted(previous_ids - current_ids) if previous else [],
        "matching_nonlisted_job_ids": closed_matching,
        "zero_view_job_ids": [role["job_id"] for role in roles if role["views"] == 0],
        "viewed_zero_applicant_job_ids": [
            role["job_id"]
            for role in roles
            if role["views"] is not None
            and role["views"] > 0
            and role["applicants"] == 0
        ],
        "data_quality": {
            "unique_active_job_ids": len(current_ids),
            "metric_coverage": {
                field: sum(role[field] is not None for role in roles)
                for field in ("views", "applicants")
            },
            "warnings": warnings,
        },
        "comparison": {
            "previous_successful_snapshot_at": previous.get("captured_at")
            if previous
            else None,
        },
    }
    snapshot["slack_text"] = slack_report(snapshot)

    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(snapshot, sort_keys=True) + "\n")
    write_result(snapshot)
    print(json.dumps(snapshot, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
