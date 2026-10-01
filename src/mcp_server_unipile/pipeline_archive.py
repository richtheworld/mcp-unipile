"""Guarded native archiving of explicitly unlinked Recruiter project records."""
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any

from . import pipeline_reconcile as inventory
from .recruiter_client import RecruiterClient

QUERY_ID = "talentHiringProjectCandidates.5a62beef423311c450bfee0f8523245f"
NATIVE_URL = "https://www.linkedin.com/talent/api/graphql"


def _identity(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = ("entity_id", "recruiter_id", "public_profile_url", "stage_id", "classification", "unlinked", "anonymized")
    return sorted(({key: r.get(key) for key in keys} for r in records), key=lambda r: r["entity_id"])


def build_plan(aid: str, project_id: str, contract_id: str, project: dict[str, Any],
               snapshot: dict[str, Any]) -> dict[str, Any]:
    if not snapshot.get("complete") or str(project.get("id")) != project_id:
        raise ValueError("A complete current project inventory is required before archiving")
    stages = project.get("pipeline", {}).get("stages", [])
    archived = [s for s in stages if str(s.get("name", "")).strip().casefold() == "archived"]
    if len(archived) != 1 or not re.fullmatch(r"[0-9]+", str(archived[0].get("id", ""))):
        raise ValueError("Expected one verified numeric Archived stage in this project")
    stage_id = archived[0]["id"]
    records = snapshot["records"]
    targets = [r for r in records if r.get("classification") == "imported_unlinked"
               and r.get("unlinked") is True and r.get("anonymized") is False
               and not r.get("public_profile_url") and r.get("stage_id") != stage_id]
    if len(targets) > 100:
        raise ValueError("Native archive batch is limited to 100 records; no write was made")
    entities = []
    for row in sorted(targets, key=lambda r: r["entity_id"]):
        match = re.fullmatch(rf"urn:li:ts_hiring_candidate:\(urn:li:ts_contract:{contract_id},urn:li:ts_hire_identity:([0-9]+)\)", row["entity_id"])
        if not match or not row.get("recruiter_id") or row.get("stage_id") not in {s.get("id") for s in stages}:
            raise ValueError("Archive target has invalid project identity or stage")
        resource = (f"urn:li:ts_hiring_project_candidate:(urn:li:ts_contract:{contract_id},"
                    f"urn:li:ts_hire_identity:{match[1]},urn:li:ts_hiring_project:(urn:li:ts_contract:{contract_id},{project_id}))")
        entities.append({"resourceKey": resource, "entity": {
            "candidateHiringStateUrn": f"urn:li:ts_hiring_state:(urn:li:ts_contract:{contract_id},{stage_id})"}})
    request = {"method": "POST", "url": NATIVE_URL,
               "query_params": {"action": "execute", "queryId": QUERY_ID},
               "body": {"queryId": QUERY_ID, "variables": {"entities": entities}}}
    content = {"schema_version": 1, "account_id": aid, "project_id": project_id,
               "project_name": project.get("name"), "contract_id": contract_id,
               "archive_stage_id": stage_id, "inventory": _identity(records),
               "targets": [{"entity_id": r["entity_id"], "recruiter_id": r["recruiter_id"], "name": r.get("name")}
                           for r in sorted(targets, key=lambda r: r["entity_id"])], "request": request}
    digest = sha256(json.dumps(content, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {**content, "confirmation_token": f"ARCHIVE_UNLINKED:{project_id}:{digest}"}


def validate_response(response: dict[str, Any], plan: dict[str, Any]) -> None:
    data = response.get("data", {})
    value = data.get("value", {}) if isinstance(data, dict) else {}
    result = value.get("data", {}) if isinstance(value, dict) else {}
    acknowledgements = result.get("batchUpdateHiringProjectCandidatesByUrns") if isinstance(result, dict) else None
    if not isinstance(acknowledgements, list) or any(isinstance(p, dict) and p.get("errors") for p in (response, data, value, result)):
        raise ValueError("Native archive response did not acknowledge the batch; read inventory before any retry")
    actual = [row.get("resourceKey") for row in acknowledgements if isinstance(row, dict)]
    expected = [row["resourceKey"] for row in plan["request"]["body"]["variables"]["entities"]]
    if len(actual) != len(expected) or set(actual) != set(expected):
        raise ValueError("Native archive response has partial or mismatched targets; read inventory before any retry")


@inventory.with_pipeline_pacing
def run(client: RecruiterClient, aid: str, project_id: str, plan_file: str, *,
        execute: bool = False, confirm: str | None = None, max_pages: int = 100) -> dict[str, Any]:
    inventory.validate_options(project_id, None, 1, max_pages)
    if not isinstance(client, RecruiterClient) or client.api_version != "v2":
        raise ValueError("Pipeline archiving requires v2")
    path = Path(plan_file)
    approved = json.loads(path.read_text()) if execute else None
    contract = inventory.resolve_contract_id(client, aid)
    before = inventory._snapshot(client, aid, project_id, contract, max_pages)
    project = client.get_project(aid, project_id)
    # Counts must still match the completed inventory immediately before planning.
    if not inventory._stage_check(project, before.get("records", []))["complete"]:
        raise ValueError("Project counts changed during archive preflight; no write was made")
    plan = build_plan(aid, project_id, contract, project, before)
    if not execute:
        path.write_text(json.dumps(plan, indent=2) + "\n")
        return {"dry_run": True, "operation": "archive-unlinked", "project_id": project_id,
                "target_count": len(plan["targets"]), "targets": plan["targets"], "plan_file": str(path),
                "execute_with": {"execute": True, "confirm": plan["confirmation_token"]}}
    if approved != plan or confirm != plan["confirmation_token"]:
        raise ValueError("Archive plan or exact confirmation token is stale or mismatched; regenerate the dry run")
    if not plan["targets"]:
        return {"success": True, "verified": True, "operation": "archive-unlinked", "archived_count": 0}
    # Native request and targets are re-created from fresh reads; never execute a file's arbitrary request.
    response = client.proxy_request(aid, plan["request"])
    validate_response(response, plan)
    after = inventory._snapshot(client, aid, project_id, contract, max_pages)
    if not after.get("complete"):
        raise ValueError("Archive submitted, but readback inventory is incomplete; inspect before any retry")
    old = {r["entity_id"]: r for r in _identity(before["records"])}
    new = {r["entity_id"]: r for r in _identity(after["records"])}
    targets = {r["entity_id"] for r in plan["targets"]}
    if set(old) != set(new):
        raise ValueError("Archive submitted, but project membership changed during verification")
    for identifier, record in new.items():
        expected = {**old[identifier], "stage_id": plan["archive_stage_id"]} if identifier in targets else old[identifier]
        if record != expected:
            raise ValueError("Archive submitted, but target or retained-record readback differs; inspect before any retry")
    return {"success": True, "verified": True, "operation": "archive-unlinked", "project_id": project_id,
            "archived_count": len(targets), "retained_records_count": len(old) - len(targets),
            "native_route": NATIVE_URL, "permanent_deletions": 0}
