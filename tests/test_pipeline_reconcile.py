import json
import os
import unittest
from unittest.mock import Mock, patch
from urllib.parse import unquote

from mcp_server_unipile import pipeline_reconcile as reconcile
from mcp_server_unipile.recruiter_cli import build_parser, execute, get_client, main
from mcp_server_unipile.recruiter_client import RecruiterClient, UnipileAPIError


def native_item(number=1, *, name="Ada Example", unlinked=False, company="Example Engines", title="Engineer"):
    first, last = name.split(" ", 1)
    context = (f"urn:li:ts_hiring_project_candidate:(urn:li:ts_contract:123,urn:li:ts_hire_identity:{number},"
               "urn:li:ts_hiring_project:(urn:li:ts_contract:123,456))")
    return {
        "entityUrn": f"urn:li:ts_hiring_candidate:(urn:li:ts_contract:123,urn:li:ts_hire_identity:{number})",
        "linkedInMemberProfileUrnResolutionResult": {
            "referenceUrn": f"urn:li:ts_profile:AE{number}", "firstName": first, "lastName": last,
            "unlinked": unlinked, "anonymized": False,
            "publicProfileUrl": None if unlinked else f"https://www.linkedin.com/in/example-{number}",
            "workExperience": [{"companyName": company, "title": title}],
            "educations": [], "skills": [],
        },
        "hiringProjectRecruitingProfile": context,
        "hiringProjectRecruitingProfileResolutionResult": {"entityUrn": context, "currentHiringProjectCandidate": {
            "candidateHiringState": "urn:li:ts_hiring_state:(urn:li:ts_contract:123,1)"}},
    }


def project(count=1):
    return {"id": "456", "name": "Example & Engineering (Test)", "pipeline": {"stages": [
        {"id": "1", "name": "Uncontacted", "candidates_count": count}]}}


def page(items=None, *, start=0, count=100, total=None):
    items = [native_item()] if items is None else items
    return {"data": {"paging": {"start": start, "count": count, "total": len(items) if total is None else total}, "elements": items}}


def client_for(items=None):
    items = [native_item()] if items is None else items
    client = RecruiterClient(api_key="synthetic-key")
    client.get_linkedin_contracts = Mock(return_value={"contracts": [
        {"product": "recruiter", "selected": True, "id": "RECRUITER_123"}]})
    client.get_project = Mock(return_value=project(len(items)))
    client.proxy_request = Mock(return_value=page(items))
    return client


class PipelineReconcileTests(unittest.TestCase):
    def test_request_matches_verified_encoding_without_interpolating_syntax(self):
        body = reconcile.request_body("456", "123", "Name),q:other & 株式会社", 100)
        self.assertEqual(body["method"], "GET")
        self.assertEqual(body["url"], reconcile.NATIVE_URL)
        self.assertTrue(body["bypass_url_encoding"])
        params = body["query_params"]
        self.assertEqual(params["start"], "100")
        self.assertIn("Name%29%2Cq%3Aother%20%26%20", params["query"])
        self.assertIn("urn%3Ali%3Ats_hiring_project%3A%28", params["requestParams"])
        self.assertEqual(unquote(params["decoration"]), reconcile.DECORATION)
        self.assertNotIn("notes", reconcile.DECORATION)
        with self.assertRaises(ValueError):
            reconcile.request_body("456),q:other", "123", "Name", 0)

    def test_classification_and_professional_allowlist(self):
        item = native_item()
        item["headers"] = {"set-cookie": "must-not-output"}
        p = item["linkedInMemberProfileUrnResolutionResult"]
        p.update({"email": "private@example.test", "notes": "secret-note", "resume": "private-resume"})
        p["workExperience"][0]["privateNotes"] = "secret-work-note"
        record = reconcile.project_record(item)
        self.assertEqual(record["classification"], "linked_with_sections")
        self.assertNotIn("secret", json.dumps(record))
        self.assertNotIn("private", json.dumps(record))
        p["workExperience"] = [{}]
        self.assertEqual(reconcile.project_record(item)["classification"], "linked_sparse")
        p["unlinked"] = True
        self.assertEqual(reconcile.project_record(item)["classification"], "imported_unlinked")
        p["anonymized"] = True
        self.assertEqual(reconcile.project_record(item)["classification"], "unknown")
        del item["linkedInMemberProfileUrnResolutionResult"]
        self.assertEqual(reconcile.project_record(item)["classification"], "resolution_error")

    def test_two_complete_equal_snapshots_stabilize_and_are_read_only(self):
        client = client_for()
        result = reconcile.run(client, "acc_example", "456", "123")
        self.assertTrue(result["inventory_complete"])
        self.assertEqual(len(result["passes"]), 2)
        self.assertEqual(client.get_project.call_count, 4)
        self.assertEqual(client.min_request_interval_seconds, 0)
        self.assertTrue(all(call.args[1]["method"] == "GET" for call in client.proxy_request.call_args_list))
        self.assertEqual(result["coverage"], {"linked_with_sections": 1})

    def test_single_pass_is_snapshot_complete_but_not_stable(self):
        result = reconcile.run(client_for(), "acc_example", "456", "123", max_passes=1)
        self.assertTrue(result["snapshot_complete"])
        self.assertFalse(result["inventory_complete"])

    def test_paginates_and_checks_second_page(self):
        client = client_for()
        items = [native_item(i, name=f"Person {i}") for i in range(101)]
        client.get_project.return_value = project(101)
        client.proxy_request.side_effect = [page(items[:100], total=101), page(items[100:], start=100, total=101)] * 2
        result = reconcile.run(client, "acc_example", "456", "123")
        self.assertTrue(result["inventory_complete"])
        self.assertEqual(result["records_count"], 101)
        self.assertEqual(result["passes"][0]["pages"][1]["requested_start"], 100)

    def test_invalid_pagination_and_duplicates_never_claim_complete(self):
        cases = [
            (page(start=100), "invalid_page_bounds"),
            (page(count=25), "invalid_page_bounds"),
            (page(total=2), "page_length_mismatch"),
            (page([native_item(), native_item()]), "duplicate_candidate_entity_id"),
            (page([{}]), "missing_candidate_entity_id"),
            ({"data": {}}, "invalid_page_shape"),
        ]
        for response, issue in cases:
            with self.subTest(issue=issue):
                client = client_for()
                client.proxy_request.return_value = response
                result = reconcile.run(client, "acc_example", "456", "123", max_passes=1)
                self.assertFalse(result["inventory_complete"])
                self.assertIn(issue, result["passes"][0]["issues"])

    def test_wrong_contract_project_and_stage_context_never_claim_complete(self):
        for field in ("entityUrn", "hiringProjectRecruitingProfile", "resolved_entity", "stage"):
            with self.subTest(field=field):
                item = native_item()
                if field in ("entityUrn", "hiringProjectRecruitingProfile"):
                    item[field] = item[field].replace("123", "999")
                elif field == "resolved_entity":
                    item["hiringProjectRecruitingProfileResolutionResult"]["entityUrn"] = item["hiringProjectRecruitingProfile"].replace("456", "999")
                else:
                    item["hiringProjectRecruitingProfileResolutionResult"]["currentHiringProjectCandidate"]["candidateHiringState"] = "urn:li:ts_hiring_state:(urn:li:ts_contract:999,1)"
                client = client_for([item])
                result = reconcile.run(client, "acc_example", "456", "123", max_passes=1)
                self.assertFalse(result["snapshot_complete"])
                self.assertEqual(result["records_count"], 0)
                result = reconcile.list_page(client, "acc_example", "456", "123", limit=100)
                self.assertFalse(result["page_valid"])
                self.assertEqual(result["items"], [])

    def test_total_drift_page_limit_and_stage_mismatch(self):
        items = [native_item(i) for i in range(100)]
        client = client_for()
        client.proxy_request.side_effect = [page(items, total=101), page([native_item(101)], start=100, total=102)]
        result = reconcile.run(client, "acc_example", "456", "123", max_passes=1)
        self.assertIn("total_drift", result["passes"][0]["issues"])
        client.proxy_request.side_effect = None
        client.proxy_request.return_value = page(items, total=101)
        result = reconcile.run(client, "acc_example", "456", "123", max_passes=1, max_pages=1)
        self.assertIn("page_limit_reached", result["passes"][0]["issues"])
        self.assertIn("stage_count_mismatch:1", result["passes"][0]["issues"])

    def test_changed_records_require_another_equal_snapshot(self):
        client = client_for()
        altered = native_item()
        altered["linkedInMemberProfileUrnResolutionResult"]["headline"] = "New role"
        client.proxy_request.side_effect = [page(), page([altered]), page([altered])]
        result = reconcile.run(client, "acc_example", "456", "123")
        self.assertTrue(result["inventory_complete"])
        self.assertEqual(len(result["passes"]), 3)

    def test_provider_failure_stops_and_preserves_prior_complete_snapshot_without_raw_detail(self):
        client = client_for()
        client.proxy_request.side_effect = [page(), UnipileAPIError(429, "rate_limit", "private-note")]
        result = reconcile.run(client, "acc_example", "456", "123")
        self.assertFalse(result["inventory_complete"])
        self.assertTrue(result["used_last_complete_snapshot"])
        self.assertEqual(len(result["passes"]), 2)
        self.assertNotIn("private-note", json.dumps(result))

    def test_name_alone_is_not_a_mapping_but_company_and_title_support_a_suggestion(self):
        imported = reconcile.project_record(native_item(2, unlinked=True))
        other = reconcile.project_record(native_item(3, company="Other Company"))
        groups, queue = reconcile.reconcile([imported, other], [])
        self.assertEqual(groups[0]["pairs"][0]["status"], "name_collision_only")
        self.assertEqual(imported["identity_status"], "unresolved")
        linked = reconcile.project_record(native_item(1))
        reconcile.reconcile([imported, linked], [])
        self.assertEqual(imported["identity_status"], "mapping_suggested")
        self.assertEqual(imported["identity_suggestions"][0]["status"], "evidence_backed")

    def evidence(self, **changes):
        row = {"candidate_id": "AE2", "public_profile_url": "https://www.linkedin.com/in/external-example",
               "source": "fiber", "match_status": "confirmed", "confidence": 0.99,
               "name": "Ada Example", "company": "Example Engines", "title": "Engineer"}
        row.update(changes)
        return {"schema_version": 1, "matches": [row]}

    def test_evidence_confirmation_requires_professional_corroboration(self):
        imported = reconcile.project_record(native_item(2, unlinked=True))
        evidence = reconcile.validate_evidence(self.evidence(company="Wrong Company"))
        reconcile.reconcile([imported], evidence)
        self.assertEqual(imported["identity_status"], "ambiguous")
        evidence = reconcile.validate_evidence(self.evidence())
        reconcile.reconcile([imported], evidence)
        self.assertEqual(imported["identity_status"], "mapping_suggested")
        evidence += reconcile.validate_evidence(self.evidence(public_profile_url="https://www.linkedin.com/in/conflicting-example"))
        reconcile.reconcile([imported], evidence)
        self.assertEqual(imported["identity_status"], "ambiguous")

    def test_evidence_rejects_unknown_fields_unsafe_urls_and_unmatched_ids(self):
        for payload in [self.evidence(notes="private"), self.evidence(confidence=float("nan")),
                        self.evidence(public_profile_url="https://linkedin.com.evil.test/in/person"),
                        self.evidence(public_profile_url="https://www.linkedin.com/in/person?cookie=x")]:
            with self.assertRaises(ValueError):
                reconcile.validate_evidence(payload)
        imported = reconcile.project_record(native_item(2, unlinked=True))
        with self.assertRaisesRegex(ValueError, "exactly one"):
            reconcile.reconcile([imported], reconcile.validate_evidence(self.evidence(candidate_id="unmatched")))

    def test_matched_recruiter_id_does_not_override_conflicting_work_history(self):
        imported = reconcile.project_record(native_item(2, unlinked=True))
        conflicting = reconcile.project_record(native_item(1, company="Different Company"))
        ev = reconcile.validate_evidence(self.evidence(matched_recruiter_id="AE1", public_profile_url=conflicting["public_profile_url"]))
        reconcile.reconcile([imported, conflicting], ev)
        self.assertEqual(imported["identity_status"], "ambiguous")

    def test_optional_contract_resolution_uses_only_the_selected_recruiter_contract(self):
        client = client_for()
        client.get_linkedin_contracts.return_value = {"contracts": [
            {"product": "recruiter", "selected": False, "id": "RECRUITER_999"},
            {"product": "sales_navigator", "selected": True, "id": "SALES_NAVIGATOR_123"},
            {"product": "recruiter", "selected": True, "id": "RECRUITER_123"}]}
        args = build_parser().parse_args(["--account-id", "acc_example", "pipeline-reconcile", "456"])
        result = execute(args, client)
        self.assertTrue(result["inventory_complete"])
        self.assertEqual(client.get_linkedin_contracts.call_count, 1)
        self.assertIn("123", client.proxy_request.call_args.args[1]["query_params"]["requestParams"])

    def test_contract_ambiguity_malformed_and_explicit_mismatch_fail_before_project_read(self):
        cases = [
            ({"contracts": []}, None),
            ({"contracts": [{"product": "recruiter", "selected": True, "id": "123"}]}, None),
            ({"contracts": [{"product": "recruiter", "selected": "true", "id": "RECRUITER_123"}]}, None),
            ({"contracts": [{"product": "recruiter", "selected": True, "id": "RECRUITER_123"}] * 2}, None),
            ({"contracts": [{"product": "recruiter", "selected": True, "id": "RECRUITER_123"}]}, "999"),
        ]
        for payload, explicit in cases:
            with self.subTest(payload=payload, explicit=explicit):
                client = client_for()
                client.get_linkedin_contracts.return_value = payload
                with self.assertRaises(ValueError):
                    reconcile.run(client, "acc_example", "456", explicit)
                client.get_project.assert_not_called()
                client.proxy_request.assert_not_called()

    def test_public_web_provenance_is_bounded_and_preserved(self):
        payload = self.evidence(source="public_web", rationale="Name, employer and role corroborated.",
                                source_urls=["https://example.com/team"])
        evidence = reconcile.validate_evidence(payload)
        imported = reconcile.project_record(native_item(2, unlinked=True))
        reconcile.reconcile([imported], evidence)
        suggestion = imported["identity_suggestions"][0]
        self.assertEqual(suggestion["source_urls"], ["https://example.com/team"])
        self.assertEqual(suggestion["status"], "evidence_backed")
        for changes in ({"rationale": "x" * 1001}, {"source_urls": ["https://example.com"] * 6},
                        {"source_urls": ["http://example.com"]}, {"source_urls": ["https://user:secret@example.com"]},
                        {"source_urls": "https://example.com"}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    reconcile.validate_evidence(self.evidence(source="public_web", **changes))

    def test_pipeline_command_uses_native_offset_page_and_never_legacy_wrapper(self):
        args = build_parser().parse_args(["--account-id", "acc_example", "pipeline", "456",
                                         "--contract-id", "123", "--offset", "25", "--limit", "25"])
        client = client_for()
        client.list_pipeline = Mock(side_effect=AssertionError("legacy route must not be used"))
        client.proxy_request.return_value = page([native_item(26)], start=25, count=25, total=26)
        result = execute(args, client)
        self.assertTrue(result["page_valid"])
        self.assertFalse(result["inventory_complete"])
        self.assertIsNone(result["next_offset"])
        self.assertEqual(result["items"][0]["recruiter_id"], "AE26")
        self.assertEqual(client.proxy_request.call_args.args[1]["query_params"]["start"], "25")
        client.list_pipeline.assert_not_called()

    def test_pipeline_legacy_filters_and_cursors_fail_before_network(self):
        for extra in (["--cursor", "old-cursor"], ["--body", '{"stage":"1"}']):
            args = build_parser().parse_args(["pipeline", "456", "--contract-id", "123", *extra])
            with patch("mcp_server_unipile.recruiter_cli.get_client") as get:
                with self.assertRaisesRegex(ValueError, "does not support"):
                    execute(args)
                get.assert_not_called()

    def test_pipeline_exit_status_distinguishes_valid_partial_page_from_failed_inventory_check(self):
        for stage_check, expected in ((None, 0), ({"complete": True}, 0), ({"complete": False}, 2)):
            with self.subTest(stage_check=stage_check):
                with patch("mcp_server_unipile.recruiter_cli.execute", return_value={"page_valid": True, "stage_check": stage_check}), patch("mcp_server_unipile.recruiter_cli.print_json"):
                    self.assertEqual(main(["pipeline", "456"]), expected)

    def test_single_page_inventory_claim_requires_fresh_project_stage_counts(self):
        client = client_for()
        client.proxy_request.return_value = page([], count=25, total=0)
        result = reconcile.list_page(client, "acc_example", "456", "123")
        self.assertTrue(result["page_valid"])
        self.assertFalse(result["inventory_complete"])
        self.assertIn("stage_count_mismatch:1", result["stage_check"]["issues"])
        self.assertEqual(client.get_project.call_count, 2)

    def test_pipeline_next_offset_is_exposed_only_for_valid_pages(self):
        client = client_for()
        client.proxy_request.return_value = page([native_item()], count=1, total=2)
        result = reconcile.list_page(client, "acc_example", "456", "123", limit=1)
        self.assertEqual(result["next_offset"], 1)
        client.proxy_request.return_value = page([native_item()], count=1, start=1, total=2)
        result = reconcile.list_page(client, "acc_example", "456", "123", limit=1)
        self.assertFalse(result["page_valid"])
        self.assertIsNone(result["next_offset"])

    def test_missing_project_shape_and_changed_readback_are_incomplete(self):
        client = client_for()
        client.get_project.return_value = {"id": "456"}
        result = reconcile.run(client, "acc_example", "456", "123", max_passes=1)
        self.assertIn("missing_project_name", result["passes"][0]["issues"])
        client = client_for()
        client.get_project.side_effect = [project(), {"id": "other"}]
        result = reconcile.run(client, "acc_example", "456", "123", max_passes=1)
        self.assertIn("project_id_mismatch", result["passes"][0]["issues"])

    def test_v1_rejected_before_credentials(self):
        args = build_parser().parse_args(["--backend", "v1", "pipeline-reconcile", "456", "--contract-id", "123"])
        with patch("mcp_server_unipile.recruiter_cli.get_client") as get:
            with self.assertRaisesRegex(ValueError, "v1"):
                execute(args)
            get.assert_not_called()

    def test_cli_pacing_is_raised_before_account_discovery(self):
        args = build_parser().parse_args(["pipeline-reconcile", "456", "--contract-id", "123"])
        client = client_for()
        def discover():
            self.assertGreaterEqual(client.min_request_interval_seconds, 5)
            return "acc_example"
        client.discover_linkedin_account = Mock(side_effect=discover)
        with patch.dict(os.environ, {}, clear=True):
            result = execute(args, client)
        self.assertTrue(result["inventory_complete"])
        with patch.dict(os.environ, {"UNIPILE_V2_API_KEY": "synthetic"}, clear=True):
            self.assertEqual(get_client(args).min_request_interval_seconds, 5)


if __name__ == "__main__":
    unittest.main()
