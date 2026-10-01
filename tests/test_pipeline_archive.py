import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from mcp_server_unipile import pipeline_archive as archive
from mcp_server_unipile import pipeline_reconcile as inventory
from mcp_server_unipile.recruiter_client import RecruiterClient
from mcp_server_unipile.recruiter_cli import build_parser, execute
from test_pipeline_reconcile import native_item


def fixture():
    records = [inventory.project_record(native_item(1, unlinked=True)),
               inventory.project_record(native_item(2, unlinked=False))]
    project = {"id": "456", "name": "Synthetic project", "pipeline": {"stages": [
        {"id": "1", "name": "uncontacted", "candidates_count": 2},
        {"id": "9", "name": "Archived", "candidates_count": 0}]}}
    return project, {"complete": True, "records": records}


def acknowledgement(plan):
    return {"data": {"value": {"data": {"batchUpdateHiringProjectCandidatesByUrns": [
        {"resourceKey": e["resourceKey"]} for e in plan["request"]["body"]["variables"]["entities"]]}}}}


class ArchiveTests(unittest.TestCase):
    def test_plan_uses_observed_native_mutation_and_only_unlinked_records(self):
        project, snapshot = fixture()
        plan = archive.build_plan("acc_test", "456", "123", project, snapshot)
        self.assertEqual(len(plan["targets"]), 1)
        self.assertEqual(plan["request"]["body"]["queryId"], archive.QUERY_ID)
        entity = plan["request"]["body"]["variables"]["entities"][0]
        self.assertEqual(entity["resourceKey"], "urn:li:ts_hiring_project_candidate:(urn:li:ts_contract:123,urn:li:ts_hire_identity:1,urn:li:ts_hiring_project:(urn:li:ts_contract:123,456))")
        self.assertEqual(entity["entity"], {"candidateHiringStateUrn": "urn:li:ts_hiring_state:(urn:li:ts_contract:123,9)"})

    def test_linked_sparse_unknown_anonymized_and_already_archived_are_preserved(self):
        p, s = fixture()
        for change in ({"classification": "linked_sparse", "unlinked": False, "public_profile_url": None},
                       {"classification": "unknown"}, {"anonymized": True},
                       {"stage_id": "9"}, {"public_profile_url": "https://www.linkedin.com/in/example"}):
            q = copy.deepcopy(s); q["records"][0].update(change)
            self.assertEqual(archive.build_plan("acc_test", "456", "123", p, q)["targets"], [])

    def test_wrong_contract_missing_archive_stage_or_incomplete_inventory_fail(self):
        p, s = fixture()
        with self.assertRaises(ValueError): archive.build_plan("acc_test", "456", "999", p, s)
        p["pipeline"]["stages"].pop()
        with self.assertRaises(ValueError): archive.build_plan("acc_test", "456", "123", p, s)
        p, s = fixture(); s["complete"] = False
        with self.assertRaises(ValueError): archive.build_plan("acc_test", "456", "123", p, s)

    def test_partial_and_error_responses_fail_without_claiming_success(self):
        p, s = fixture(); plan = archive.build_plan("acc_test", "456", "123", p, s)
        archive.validate_response(acknowledgement(plan), plan)
        for result in ({}, {"data": {"value": {"errors": ["failed"]}}},
                       acknowledgement({"request": {"body": {"variables": {"entities": []}}}})):
            with self.assertRaises(ValueError): archive.validate_response(result, plan)

    def test_dry_run_no_write_then_exact_token_execute_and_readback(self):
        p, before = fixture(); c = RecruiterClient(api_key="synthetic", min_request_interval_seconds=1.1)
        after = copy.deepcopy(before); after["records"][0]["stage_id"] = "9"
        c.get_project = Mock(return_value=p)
        plan = archive.build_plan("acc_test", "456", "123", p, before)
        c.proxy_request = Mock(return_value=acknowledgement(plan))
        with tempfile.TemporaryDirectory() as directory, patch.object(inventory, "resolve_contract_id", return_value="123"), patch.object(inventory, "_snapshot", side_effect=[before, before, before, after]):
            path = str(Path(directory) / "plan.json")
            dry = archive.run(c, "acc_test", "456", path)
            c.proxy_request.assert_not_called()
            with self.assertRaises(ValueError): archive.run(c, "acc_test", "456", path, execute=True, confirm="wrong")
            c.proxy_request.assert_not_called()
            result = archive.run(c, "acc_test", "456", path, execute=True, confirm=dry["execute_with"]["confirm"])
            self.assertTrue(result["verified"]); self.assertEqual(result["archived_count"], 1)
        self.assertEqual(c.min_request_interval_seconds, 1.1)

    def test_tampered_plan_request_cannot_run(self):
        p, s = fixture(); c = RecruiterClient(api_key="synthetic")
        c.get_project = Mock(return_value=p); c.proxy_request = Mock()
        plan = archive.build_plan("acc_test", "456", "123", p, s); plan["request"]["url"] = "https://untrusted.invalid"
        with tempfile.TemporaryDirectory() as directory, patch.object(inventory, "resolve_contract_id", return_value="123"), patch.object(inventory, "_snapshot", return_value=s):
            path = Path(directory) / "plan.json"; path.write_text(json.dumps(plan))
            with self.assertRaises(ValueError): archive.run(c, "acc_test", "456", str(path), execute=True, confirm=plan["confirmation_token"])
            c.proxy_request.assert_not_called()

    def test_cli_pacing_restored_after_exception(self):
        c = RecruiterClient(api_key="synthetic", min_request_interval_seconds=1.1)
        args = build_parser().parse_args(["--backend", "v2", "--account-id", "acc_test", "pipeline", "456"])
        with patch.object(c, "get_linkedin_contracts", side_effect=ValueError("synthetic failure")):
            with self.assertRaises(ValueError): execute(args, c)
        self.assertEqual(c.min_request_interval_seconds, 1.1)

    def test_anonymized_fields_and_provenance_tokens_are_not_exported(self):
        item = native_item(); item["linkedInMemberProfileUrnResolutionResult"]["anonymized"] = True
        record = inventory.project_record(item)
        self.assertEqual(record["name"], ""); self.assertIsNone(record["public_profile_url"])
        self.assertIsNone(record["recruiter_id"]); self.assertEqual(record["work_experience"], [])
        for url in ("https://example.com/evidence?token=secret", "https://example.com/evidence#secret"):
            with self.assertRaises(ValueError): inventory.validate_evidence({"schema_version": 1, "matches": [{"candidate_id": "AE1", "source": "browser", "match_status": "unresolved", "confidence": 0, "source_urls": [url]}]})

    def test_changed_retained_record_fails_final_verification(self):
        p, before = fixture(); after = copy.deepcopy(before)
        after["records"][0]["stage_id"] = "9"; after["records"][1]["stage_id"] = "9"
        c = RecruiterClient(api_key="synthetic"); c.get_project = Mock(return_value=p)
        plan = archive.build_plan("acc_test", "456", "123", p, before)
        c.proxy_request = Mock(return_value=acknowledgement(plan))
        with tempfile.TemporaryDirectory() as directory, patch.object(inventory, "resolve_contract_id", return_value="123"), patch.object(inventory, "_snapshot", side_effect=[before, after]):
            path = Path(directory) / "plan.json"; path.write_text(json.dumps(plan))
            with self.assertRaisesRegex(ValueError, "retained-record readback"):
                archive.run(c, "acc_test", "456", str(path), execute=True, confirm=plan["confirmation_token"])
            self.assertEqual(c.proxy_request.call_count, 1)

    def test_changed_counts_block_before_mutation(self):
        p, before = fixture(); p["pipeline"]["stages"][0]["candidates_count"] = 3
        c = RecruiterClient(api_key="synthetic"); c.get_project = Mock(return_value=p); c.proxy_request = Mock()
        with tempfile.TemporaryDirectory() as directory, patch.object(inventory, "resolve_contract_id", return_value="123"), patch.object(inventory, "_snapshot", return_value=before):
            with self.assertRaisesRegex(ValueError, "counts changed"):
                archive.run(c, "acc_test", "456", str(Path(directory) / "plan.json"))
            c.proxy_request.assert_not_called()
