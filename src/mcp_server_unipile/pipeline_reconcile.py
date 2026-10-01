"""Bounded, read-only native Recruiter pipeline inventory and identity suggestions.

The native decoration is an explicit professional-data allowlist. Responses are
projected again here so unexpected fields or proxy response headers never escape.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import re
import unicodedata
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from .recruiter_client import RecruiterClient, UnipileAPIError

NATIVE_URL = "https://www.linkedin.com/talent/search/api/talentRecruiterSearchHits"
DECORATION = (
    "(entityUrn,linkedInMemberProfileUrn~(entityUrn,anonymized,referenceUrn,"
    "firstName,lastName,headline,publicProfileUrl,unlinked,location(displayName),"
    "industryName,workExperience*(companyName,title,startDateOn,endDateOn),"
    "educations*(schoolName,degreeName,startDateOn,endDateOn),skills*(skillName)),"
    "hiringProjectRecruitingProfile~(entityUrn,currentHiringProjectCandidate("
    "candidateHiringState,previousCandidateHiringState)))"
)


def validate_options(project_id: str, contract_id: str | None, max_passes: int, max_pages: int) -> None:
    if not re.fullmatch(r"[0-9]+", project_id) or (contract_id is not None and not re.fullmatch(r"[0-9]+", contract_id)):
        raise ValueError("Project and contract IDs must be numeric native Recruiter IDs")
    if not 1 <= max_passes <= 5 or not 1 <= max_pages <= 1000:
        raise ValueError("Use 1–5 passes and 1–1000 pages per pass")


def resolve_contract_id(client: RecruiterClient, aid: str, explicit: str | None = None) -> str:
    """Use the selected Recruiter seat only; never select or change a contract."""
    payload = client.get_linkedin_contracts(aid)
    contracts = payload.get("contracts") if isinstance(payload, dict) else None
    if not isinstance(contracts, list):
        raise ValueError("LinkedIn contract lookup did not return a contracts list")
    selected = [item for item in contracts if isinstance(item, dict)
                and item.get("product") == "recruiter" and item.get("selected") is True]
    if len(selected) != 1:
        raise ValueError("Expected exactly one selected Recruiter contract")
    identifier = selected[0].get("id")
    match = re.fullmatch(r"RECRUITER_([0-9]+)", identifier) if isinstance(identifier, str) else None
    if not match:
        raise ValueError("Selected Recruiter contract ID has an unsupported format")
    numeric = match[1]
    if explicit is not None and explicit != numeric:
        raise ValueError("Explicit --contract-id does not match the selected Recruiter contract")
    return numeric


def request_body(project_id: str, contract_id: str, name: str | None, start: int, count: int = 100) -> dict[str, Any]:
    validate_options(project_id, contract_id, 1, 1)
    if contract_id is None:
        raise ValueError("Resolve the selected Recruiter contract before building a request")
    if not isinstance(name, str) or not name.strip() or start < 0 or not 1 <= count <= 100:
        raise ValueError("A project name and non-negative page start are required")
    urn = quote(f"urn:li:ts_hiring_project:(urn:li:ts_contract:{contract_id},{project_id})", safe="")
    return {
        "method": "GET", "url": NATIVE_URL, "bypass_url_encoding": True,
        "query_params": {
            "q": "pipelineSearch",
            "query": f"(hiringProjects:List((text:{quote(name, safe='')},entity:{urn})),"
                     "facetSelections:List(),facets:List(),capSearchSortBy:HIRING_CANDIDATE_LAST_UPDATED_DATE)",
            "requestParams": f"(hiringProject:{urn},doFacetCounting:true,doFacetDecoration:true)",
            "count": str(count), "start": str(start), "decoration": quote(DECORATION, safe=""),
        },
    }


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _normal(value: str | None) -> str:
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", value or "").casefold()))


def canonical_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        url = urlsplit(value)
        slug = unquote(url.path.rstrip("/").removeprefix("/in/"))
        if (url.scheme != "https" or url.hostname not in {"linkedin.com", "www.linkedin.com"}
                or url.username or url.password or url.port or url.query or url.fragment
                or not url.path.startswith("/in/") or not re.fullmatch(r"[\w-]+", slug)):
            return None
    except ValueError:
        return None
    return "https://www.linkedin.com/in/" + quote(slug, safe="-")


def _date(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    return {key: value[key] for key in ("year", "month", "day") if type(value.get(key)) is int}


def _sections(profile: dict[str, Any], field: str, fields: tuple[str, ...]) -> list[dict[str, Any]]:
    source = profile.get(field)
    if not isinstance(source, list):
        return []
    rows = []
    for item in source:
        if isinstance(item, dict):
            row = {key: (_date(item[key]) if key.endswith("DateOn") else _text(item[key]))
                   for key in fields if key in item}
            row = {key: value for key, value in row.items() if value is not None and value != {}}
            if row:
                rows.append(row)
    return rows


def _stage_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"urn:li:ts_hiring_state:\(urn:li:ts_contract:[0-9]+,([0-9]+)\)", value or "")
    return match[1] if match else None


def context_issue(item: dict[str, Any], project_id: str, contract_id: str) -> str | None:
    """Require provider identifiers to prove membership in the requested project."""
    entity = item.get("entityUrn")
    match = re.fullmatch(
        rf"urn:li:ts_hiring_candidate:\(urn:li:ts_contract:{re.escape(contract_id)},urn:li:ts_hire_identity:([0-9]+)\)",
        entity,
    ) if isinstance(entity, str) else None
    if not match:
        return "candidate_contract_or_identity_mismatch"
    expected = (f"urn:li:ts_hiring_project_candidate:(urn:li:ts_contract:{contract_id},"
                f"urn:li:ts_hire_identity:{match[1]},urn:li:ts_hiring_project:(urn:li:ts_contract:{contract_id},{project_id}))")
    recruiting = item.get("hiringProjectRecruitingProfileResolutionResult")
    if not isinstance(recruiting, dict):
        return "missing_candidate_project_context"
    if item.get("hiringProjectRecruitingProfile") != expected or recruiting.get("entityUrn") != expected:
        return "candidate_project_context_mismatch"
    current = recruiting.get("currentHiringProjectCandidate")
    if isinstance(current, dict):
        for key in ("candidateHiringState", "previousCandidateHiringState"):
            value = current.get(key)
            if value is not None and (not isinstance(value, str) or not re.fullmatch(
                rf"urn:li:ts_hiring_state:\(urn:li:ts_contract:{re.escape(contract_id)},[0-9]+\)", value
            )):
                return "candidate_stage_contract_mismatch"
    return None


def project_record(item: dict[str, Any]) -> dict[str, Any]:
    profile = item.get("linkedInMemberProfileUrnResolutionResult")
    resolved = isinstance(profile, dict) and bool(profile)
    profile = profile if isinstance(profile, dict) else {}
    recruiting = item.get("hiringProjectRecruitingProfileResolutionResult")
    recruiting = recruiting if isinstance(recruiting, dict) else {}
    current = recruiting.get("currentHiringProjectCandidate")
    current = current if isinstance(current, dict) else {}
    reference = _text(profile.get("referenceUrn")) or ""
    recruiter_id = reference.removeprefix("urn:li:ts_profile:") if reference.startswith("urn:li:ts_profile:") else None
    work = _sections(profile, "workExperience", ("companyName", "title", "startDateOn", "endDateOn"))
    education = _sections(profile, "educations", ("schoolName", "degreeName", "startDateOn", "endDateOn"))
    skills = _sections(profile, "skills", ("skillName",))
    url = canonical_url(profile.get("publicProfileUrl"))
    unlinked, anonymized = profile.get("unlinked"), profile.get("anonymized")
    if not resolved:
        classification = "resolution_error"
    elif anonymized is True:
        classification = "unknown"
    elif unlinked is True:
        classification = "imported_unlinked"
    elif unlinked is False and recruiter_id:
        classification = "linked_with_sections" if any((work, education, skills)) else "linked_sparse"
    else:
        classification = "unknown"
    location = profile.get("location")
    return {
        "entity_id": _text(item.get("entityUrn")), "recruiter_id": recruiter_id,
        "name": " ".join(filter(None, (_text(profile.get("firstName")), _text(profile.get("lastName"))))),
        "headline": _text(profile.get("headline")), "public_profile_url": url,
        "unlinked": unlinked if type(unlinked) is bool else None,
        "anonymized": anonymized if type(anonymized) is bool else None,
        "location": _text(location.get("displayName")) if isinstance(location, dict) else None,
        "industry": _text(profile.get("industryName")),
        "work_experience": work, "education": education, "skills": skills,
        "section_counts": {"work_experience": len(work), "education": len(education), "skills": len(skills)},
        "section_fields_returned": {"work_experience": isinstance(profile.get("workExperience"), list),
                                    "education": isinstance(profile.get("educations"), list),
                                    "skills": isinstance(profile.get("skills"), list)},
        "classification": classification, "stage_id": _stage_id(current.get("candidateHiringState")),
        "previous_stage_id": _stage_id(current.get("previousCandidateHiringState")),
    }


def validate_evidence(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if not payload:
        return []
    if set(payload) != {"schema_version", "matches"} or payload["schema_version"] != 1 or not isinstance(payload["matches"], list):
        raise ValueError("Evidence requires schema_version: 1 and a matches array")
    allowed = {"candidate_id", "public_profile_url", "source", "match_status", "confidence", "name", "company", "title", "matched_recruiter_id", "rationale", "source_urls"}
    required = {"candidate_id", "source", "match_status", "confidence"}
    clean = []
    for row in payload["matches"]:
        if not isinstance(row, dict) or not required <= set(row) or set(row) - allowed:
            raise ValueError("Evidence entry has missing or unsupported fields")
        if any(not isinstance(value, str) or len(value) > 1000 for key, value in row.items() if key not in {"confidence", "source_urls"}):
            raise ValueError("Evidence text fields must be strings of at most 1000 characters")
        if not row["candidate_id"] or row["source"] not in {"fiber", "browser", "linkedin_api", "public_web"}:
            raise ValueError("Evidence requires an exact candidate ID and source fiber, browser, linkedin_api or public_web")
        if row["match_status"] not in {"confirmed", "possible", "unresolved"}:
            raise ValueError("Evidence match_status must be confirmed, possible or unresolved")
        confidence = row["confidence"]
        if type(confidence) not in (int, float) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError("Evidence confidence must be a finite number from 0 to 1")
        row = dict(row)
        if "source_urls" in row:
            urls = row["source_urls"]
            if not isinstance(urls, list) or len(urls) > 5:
                raise ValueError("Evidence source_urls must contain at most five HTTPS URLs")
            for value in urls:
                if not isinstance(value, str) or len(value) > 2000 or any(c.isspace() for c in value):
                    raise ValueError("Evidence source URL must be a string of at most 2000 characters")
                try:
                    parsed = urlsplit(value)
                    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port:
                        raise ValueError("invalid provenance URL")
                except ValueError as error:
                    raise ValueError("Evidence source URLs must use HTTPS without credentials or a custom port") from error
        if "public_profile_url" in row:
            url = canonical_url(row["public_profile_url"])
            if not url:
                raise ValueError("Evidence URL must be a canonical HTTPS LinkedIn /in/ profile URL")
            row["public_profile_url"] = url
        elif row["match_status"] != "unresolved":
            raise ValueError("Matched evidence requires public_profile_url")
        clean.append(row)
    return clean


def _jobs(record: dict[str, Any]) -> set[tuple[str, str]]:
    return {(_normal(row.get("companyName")), _normal(row.get("title")))
            for row in record["work_experience"] if row.get("companyName") and row.get("title")}


def reconcile(records: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_id, by_name = defaultdict(list), defaultdict(list)
    for record in records:
        for identifier in {record["entity_id"], record["recruiter_id"]} - {None}:
            by_id[identifier].append(record)
        if _normal(record["name"]):
            by_name[_normal(record["name"])].append(record)
        record["identity_suggestions"] = []
    groups = []
    for rows in by_name.values():
        if len(rows) < 2:
            continue
        pairs = []
        for i, left in enumerate(rows):
            for right in rows[i + 1:]:
                corroborated = bool(_jobs(left) & _jobs(right))
                pairs.append({"entity_ids": [left["entity_id"], right["entity_id"]],
                              "status": "possible_duplicate" if corroborated else "name_collision_only",
                              "basis": "same_name_company_title" if corroborated else "name_only"})
                if corroborated:
                    for source, target in ((left, right), (right, left)):
                        if source["classification"] == "imported_unlinked" and target["public_profile_url"]:
                            source["identity_suggestions"].append({"public_profile_url": target["public_profile_url"],
                                "matched_recruiter_id": target["recruiter_id"], "source": "pipeline",
                                "status": "evidence_backed", "basis": "same_name_company_title"})
        groups.append({"name": rows[0]["name"], "pairs": pairs})
    for row in evidence:
        targets = by_id.get(row["candidate_id"], [])
        if len(targets) != 1:
            raise ValueError("Evidence candidate_id must identify exactly one record in the current inventory")
        target = targets[0]
        matched = by_id.get(row.get("matched_recruiter_id"), [])
        if row.get("matched_recruiter_id") and (len(matched) != 1 or matched[0]["recruiter_id"] != row["matched_recruiter_id"] or matched[0]["public_profile_url"] != row.get("public_profile_url")):
            raise ValueError("Evidence matched_recruiter_id must match one inventory profile and its public URL")
        exact = bool(target["public_profile_url"] and target["public_profile_url"] == row.get("public_profile_url"))
        corroborated = bool(_normal(row.get("name")) and _normal(target["name"]) == _normal(row.get("name"))
                            and (_normal(row.get("company")), _normal(row.get("title"))) in _jobs(target))
        if matched:
            corroborated = corroborated and _normal(matched[0]["name"]) == _normal(target["name"]) and bool(_jobs(matched[0]) & _jobs(target))
        status = "confirmed_identifier" if exact else "evidence_backed" if corroborated and row["match_status"] == "confirmed" else "ambiguous"
        if row["match_status"] == "unresolved":
            status = "unresolved"
        target["identity_suggestions"].append({**row, "status": status,
            "basis": "exact_public_url" if exact else "same_name_company_title" if corroborated else "insufficient_identity_evidence"})
    queue = []
    for record in records:
        suggestions = record["identity_suggestions"]
        urls = {s["public_profile_url"] for s in suggestions if s.get("public_profile_url")}
        if len(urls) > 1:
            for suggestion in suggestions:
                suggestion["status"] = "ambiguous"
        record["identity_status"] = ("linked" if record["classification"].startswith("linked_")
            else "mapping_suggested" if any(s["status"] in {"evidence_backed", "confirmed_identifier"} for s in suggestions)
            else "ambiguous" if suggestions and urls else "unresolved")
        if record["classification"] != "linked_with_sections":
            queue.append({"entity_id": record["entity_id"], "recruiter_id": record["recruiter_id"],
                          "classification": record["classification"], "identity_status": record["identity_status"],
                          "action": "verify_full_profile" if record["classification"] == "linked_sparse" else
                                    "review_mapping" if record["identity_status"] == "mapping_suggested" else "browser_or_fiber_identity_check"})
    return groups, queue


def _stage_check(project: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    pipeline = project.get("pipeline")
    stages = pipeline.get("stages") if isinstance(pipeline, dict) else None
    if not isinstance(stages, list):
        return {"complete": False, "issues": ["missing_project_stages"], "counts": []}
    seen, counts, issues = set(), [], []
    actual = Counter(row["stage_id"] for row in records)
    for stage in stages:
        if not isinstance(stage, dict) or not isinstance(stage.get("id"), str) or stage["id"] in seen or type(stage.get("candidates_count")) is not int or stage["candidates_count"] < 0:
            issues.append("invalid_project_stage")
            continue
        sid = stage["id"]
        seen.add(sid)
        expected = stage["candidates_count"]
        counts.append({"id": sid, "name": _text(stage.get("name")), "expected": expected, "observed": actual[sid]})
        if expected != actual[sid]:
            issues.append("stage_count_mismatch:" + sid)
    if set(actual) - seen:
        issues.append("unknown_or_missing_candidate_stage")
    return {"complete": not issues, "issues": issues, "counts": counts}


def list_page(client: RecruiterClient, aid: str, project_id: str, contract_id: str | None = None,
              *, limit: int = 25, offset: int = 0) -> dict[str, Any]:
    """Native replacement for the failing public pipeline wrapper, one page only."""
    validate_options(project_id, contract_id, 1, 1)
    if not 1 <= limit <= 100 or offset < 0:
        raise ValueError("Native pipeline requires limit 1–100 and non-negative offset")
    if not isinstance(client, RecruiterClient) or client.api_version != "v2":
        raise ValueError("Native pipeline requires v2")
    client.min_request_interval_seconds = max(5.0, client.min_request_interval_seconds)
    contract_id = resolve_contract_id(client, aid, contract_id)
    project = client.get_project(aid, project_id)
    if not isinstance(project, dict) or str(project.get("id")) != project_id:
        raise ValueError("Project lookup returned a different project ID")
    response = client.proxy_request(aid, request_body(project_id, contract_id, project.get("name"), offset, limit))
    data = response.get("data") if isinstance(response, dict) else None
    paging = data.get("paging") if isinstance(data, dict) else None
    elements = data.get("elements") if isinstance(data, dict) else None
    issues, records, safe_paging = [], [], {}
    if not isinstance(paging, dict) or not isinstance(elements, list) or any(type(paging.get(k)) is not int for k in ("start", "count", "total")):
        issues.append("invalid_page_shape")
    else:
        safe_paging = {k: paging[k] for k in ("start", "count", "total")}
        if paging["start"] != offset or paging["count"] != limit or paging["total"] < 0:
            issues.append("invalid_page_bounds")
        elif len(elements) != min(limit, max(0, paging["total"] - offset)):
            issues.append("page_length_mismatch")
        seen = set()
        for item in elements:
            if not isinstance(item, dict) or not _text(item.get("entityUrn")):
                issues.append("missing_candidate_entity_id")
            elif context_issue(item, project_id, contract_id):
                issues.append(context_issue(item, project_id, contract_id))
            elif item["entityUrn"] in seen:
                issues.append("duplicate_candidate_entity_id")
            else:
                seen.add(item["entityUrn"])
                records.append(project_record(item))
    valid = not issues
    covers_inventory = valid and offset == 0 and len(records) == safe_paging.get("total")
    stage_check = None
    if covers_inventory:
        current_project = client.get_project(aid, project_id)
        if not isinstance(current_project, dict) or str(current_project.get("id")) != project_id:
            stage_check = {"complete": False, "issues": ["project_id_mismatch"], "counts": []}
        else:
            stage_check = _stage_check(current_project, records)
    next_offset = offset + len(records)
    return {"api_version": "v2", "read_only": True, "project_id": project_id,
            "native_route": NATIVE_URL, "items": records, "paging": safe_paging,
            "page_valid": valid, "issues": issues,
            "inventory_complete": covers_inventory and bool(stage_check and stage_check["complete"]),
            "stage_check": stage_check,
            "next_offset": next_offset if valid and next_offset < safe_paging["total"] else None}


def _snapshot(client: RecruiterClient, aid: str, project_id: str, contract_id: str, max_pages: int) -> dict[str, Any]:
    records, pages, issues, seen = [], [], [], set()
    total, project = None, {}
    try:
        project = client.get_project(aid, project_id)
        if not isinstance(project, dict) or str(project.get("id")) != project_id:
            return {"complete": False, "records": [], "pages": [], "issues": ["project_id_mismatch"]}
        if not _text(project.get("name")):
            return {"complete": False, "records": [], "pages": [], "issues": ["missing_project_name"]}
        for page_number in range(max_pages):
            start = page_number * 100
            response = client.proxy_request(aid, request_body(project_id, contract_id, project.get("name"), start))
            data = response.get("data") if isinstance(response, dict) else None
            paging = data.get("paging") if isinstance(data, dict) else None
            elements = data.get("elements") if isinstance(data, dict) else None
            if not isinstance(paging, dict) or not isinstance(elements, list) or any(type(paging.get(k)) is not int for k in ("start", "count", "total")):
                issues.append("invalid_page_shape")
                break
            pages.append({"requested_start": start, "start": paging["start"], "count": paging["count"], "total": paging["total"], "returned": len(elements)})
            if paging["start"] != start or paging["count"] != 100 or paging["total"] < 0:
                issues.append("invalid_page_bounds")
                break
            if total is not None and total != paging["total"]:
                issues.append("total_drift")
                break
            total = paging["total"]
            if len(elements) != min(100, max(0, total - start)):
                issues.append("page_length_mismatch")
                break
            for item in elements:
                if not isinstance(item, dict) or not _text(item.get("entityUrn")):
                    issues.append("missing_candidate_entity_id")
                    continue
                context_error = context_issue(item, project_id, contract_id)
                if context_error:
                    issues.append(context_error)
                    continue
                if item["entityUrn"] in seen:
                    issues.append("duplicate_candidate_entity_id")
                    continue
                seen.add(item["entityUrn"])
                records.append(project_record(item))
            if issues or len(records) == total:
                break
        else:
            issues.append("page_limit_reached")
        # Read counts again after paging: edits while traversing remain visible.
        project = client.get_project(aid, project_id)
        if not isinstance(project, dict) or str(project.get("id")) != project_id:
            return {"complete": False, "records": records, "pages": pages, "issues": [*issues, "project_id_mismatch"]}
    except UnipileAPIError as error:
        return {"complete": False, "records": records, "pages": pages, "issues": [*issues, "provider_error"],
                "provider_error": {"status_code": error.status_code, "type": error.error_type}, "stop": True}
    stage_check = _stage_check(project, records)
    issues.extend(stage_check["issues"])
    if total is None or len(records) != total:
        issues.append("inventory_count_mismatch")
    return {"complete": not issues, "records": records, "pages": pages, "issues": issues,
            "total": total, "project_name": _text(project.get("name")), "stage_check": stage_check}


def run(client: RecruiterClient, aid: str, project_id: str, contract_id: str | None = None, *, max_passes: int = 3,
        max_pages: int = 100, evidence: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    validate_options(project_id, contract_id, max_passes, max_pages)
    if not isinstance(client, RecruiterClient) or client.api_version != "v2":
        raise ValueError("Pipeline reconciliation requires v2")
    client.min_request_interval_seconds = max(5.0, client.min_request_interval_seconds)
    contract_id = resolve_contract_id(client, aid, contract_id)
    passes, previous, stable, last_complete = [], None, False, None
    snapshot: dict[str, Any] = {"records": [], "complete": False}
    for number in range(1, max_passes + 1):
        started_at = datetime.now(timezone.utc).isoformat()
        snapshot = _snapshot(client, aid, project_id, contract_id, max_pages)
        fingerprint = sha256(json.dumps({"records": sorted(snapshot["records"], key=lambda r: r["entity_id"]),
                                        "stage_check": snapshot.get("stage_check"),
                                        "project_name": snapshot.get("project_name")}, sort_keys=True).encode()).hexdigest()
        proof = {key: value for key, value in snapshot.items() if key != "records"}
        proof.update({"pass": number, "started_at": started_at,
                      "finished_at": datetime.now(timezone.utc).isoformat(),
                      "fingerprint": fingerprint, "record_count": len(snapshot["records"])})
        passes.append(proof)
        stable = snapshot["complete"] and fingerprint == previous
        previous = fingerprint if snapshot["complete"] else None
        if snapshot["complete"]:
            last_complete = snapshot
        if stable or snapshot.get("stop"):
            break
    selected = snapshot if stable or last_complete is None else last_complete
    records = selected["records"]
    groups, queue = reconcile(records, evidence or [])
    return {"schema_version": 1, "read_only": True, "api_version": "v2",
            "observed_at": datetime.now(timezone.utc).isoformat(), "project_id": project_id,
            "project_name": selected.get("project_name"), "native_route": NATIVE_URL,
            "inventory_complete": stable, "snapshot_complete": selected["complete"],
            "stabilized": stable, "used_last_complete_snapshot": selected is not snapshot,
            "selected_snapshot_pass": next(p["pass"] for p in reversed(passes) if p["complete"]) if selected["complete"] else len(passes),
            "records_count": len(records), "coverage": dict(Counter(r["classification"] for r in records)),
            "identity_status_counts": dict(Counter(r["identity_status"] for r in records)),
            "passes": passes, "records": records, "same_name_groups": groups, "next_actions": queue,
            "limits": ["Two consecutive complete equal snapshots are required for inventory_complete.",
                       "Section presence is not proof of a complete LinkedIn profile.",
                       "Identity suggestions never merge records or write to LinkedIn.",
                       "External evidence is supplied by the caller; this command does not contact Fiber or automate a browser."]}
