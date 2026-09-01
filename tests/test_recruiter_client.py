import json
import os
import unittest
from unittest.mock import Mock

from mcp_server_unipile.recruiter_cli import build_parser, execute
from mcp_server_unipile.recruiter_client import (
    RecruiterClient,
    UnipileAPIError,
    V1RecruiterClient,
    normalize_base_url,
    normalize_profile_identifier,
    profile_identifier_schema,
)


class FakeResponse:
    def __init__(self, status=200, payload=None, headers=None):
        self.status_code = status
        self._payload = payload or {}
        self.headers = headers or {}
        self.ok = 200 <= status < 300
        self.reason = "failure" if not self.ok else "OK"
        self.content = json.dumps(self._payload).encode()

    def json(self):
        return self._payload


class RecruiterClientTests(unittest.TestCase):
    def test_normalization_and_v2_enforcement(self):
        self.assertEqual(normalize_base_url("api.example.com:123"), "https://api.example.com:123")
        with self.assertRaisesRegex(ValueError, "requires base URL"):
            RecruiterClient(api_key="secret", base_url="https://api37.unipile.com:123")
        self.assertEqual(RecruiterClient(api_key="secret").api_version, "v2")

    def test_legacy_account_ids_are_rejected(self):
        client = RecruiterClient(api_key="secret")
        with self.assertRaisesRegex(ValueError, "must start with acc_"):
            client.get_project("legacy_MESSAGING", "project-1")

    def test_profile_identifier_normalizes_public_and_recruiter_profile_urls(self):
        self.assertEqual(
            normalize_profile_identifier("https://www.linkedin.com/in/ada-lovelace/?x=1"),
            "ada-lovelace",
        )
        self.assertEqual(
            normalize_profile_identifier(
                "https://www.linkedin.com/talent/profile/AE-recruiter-id?searchRequestId=1"
            ),
            "AE-recruiter-id",
        )
        self.assertEqual(
            normalize_profile_identifier(
                "https://www.linkedin.com/recruiter/profile/476162262,HHNH,name"
            ),
            "476162262",
        )
        with self.assertRaisesRegex(ValueError, "search-url command"):
            normalize_profile_identifier(
                "https://www.linkedin.com/talent/search?keywords=ada"
            )
        for encoded_delimiter in ("%2F", "%3F", "%23"):
            with self.subTest(encoded_delimiter=encoded_delimiter):
                with self.assertRaisesRegex(ValueError, "encoded URL delimiter"):
                    normalize_profile_identifier(
                        f"https://www.linkedin.com/in/ada{encoded_delimiter}admin"
                    )

        client = RecruiterClient(api_key="secret")
        client._request = Mock(return_value={"id": "ACoA-classic-id"})
        client.get_profile(
            "acc_123", "https://www.linkedin.com/in/ada-lovelace/", "classic"
        )
        self.assertEqual(
            client._request.call_args.args[1],
            "/v2/acc_123/users/ada-lovelace",
        )

    def test_profile_identifier_schema_places_inputs_at_the_v2_boundary(self):
        recruiter = profile_identifier_schema(
            "https://www.linkedin.com/talent/profile/AE-recruiter-id?searchRequestId=1"
        )
        public = profile_identifier_schema("https://www.linkedin.com/in/ada-lovelace/")

        self.assertEqual(recruiter["input_type"], "recruiter_profile_url")
        self.assertEqual(recruiter["normalized_identifier"], "AE-recruiter-id")
        self.assertEqual(
            recruiter["v2_request"],
            {
                "method": "GET",
                "path_template": "/v2/{account_id}/users/{user_id}",
                "path_params": {"user_id": "AE-recruiter-id"},
                "query": {"variant": "linkedin_recruiter"},
            },
        )
        self.assertEqual(public["input_type"], "public_profile_url")
        self.assertEqual(public["normalized_identifier"], "ada-lovelace")

    def test_convert_identifier_resolves_to_canonical_v2_record(self):
        client = RecruiterClient(api_key="secret")
        client.resolve_recruiter_profile = Mock(
            return_value=(
                {
                    "id": "https://www.linkedin.com/talent/profile/AE-canonical",
                    "public_identifier": "ada-lovelace",
                },
                1,
            )
        )

        result = client.convert_profile_identifier(
            "acc_123",
            "https://www.linkedin.com/talent/profile/AE-requested",
        )

        self.assertEqual(result["normalized_identifier"], "AE-requested")
        self.assertEqual(result["canonical_identity"]["provider_id"], "AE-canonical")
        self.assertEqual(
            result["canonical_identity"]["public_identifier"], "ada-lovelace"
        )
        self.assertEqual(result["canonical_identity"]["profile_calls"], 1)

    def test_v2_save_candidate_payload(self):
        session = Mock()
        session.request.return_value = FakeResponse(payload={"success": True})
        client = RecruiterClient(api_key="secret", session=session)
        client.save_candidate("acc_123", "project-1", "candidate-1", "stage-1")
        call = session.request.call_args
        self.assertIn("/v2/acc_123/linkedin/recruiter/projects/project-1/pipeline/candidate/save", call.args[1])
        self.assertEqual(call.kwargs["json"], {"candidate_id": "candidate-1", "stage_id": "stage-1"})

    def test_v2_account_discovery_uses_running_linkedin_account(self):
        session = Mock()
        session.request.return_value = FakeResponse(
            payload={
                "items": [
                    {"id": "acc_123", "provider": "LINKEDIN", "status": "RUNNING"},
                    {"id": "acc_456", "provider": "GOOGLE", "status": "RUNNING"},
                ]
            }
        )
        client = RecruiterClient(api_key="secret", session=session)
        self.assertEqual(client.discover_linkedin_account(), "acc_123")
        call = session.request.call_args
        self.assertTrue(call.args[1].endswith("/v2/accounts/"))
        self.assertEqual(call.kwargs["params"]["provider"], "linkedin")

    def test_api_error_is_credential_safe(self):
        session = Mock()
        session.request.return_value = FakeResponse(
            status=429,
            payload={
                "type": "api/too_many_requests",
                "detail": "slow down",
                "req_id": "req-test-123",
            },
            headers={"Retry-After": "60"},
        )
        client = RecruiterClient(api_key="secret", session=session)
        with self.assertRaises(UnipileAPIError) as caught:
            client.list_projects("acc_123")
        self.assertEqual(caught.exception.retry_after, "60")
        self.assertEqual(caught.exception.request_id, "req-test-123")
        self.assertEqual(caught.exception.as_dict()["request_id"], "req-test-123")
        self.assertNotIn("secret", str(caught.exception))

    def test_v2_client_paces_consecutive_provider_requests(self):
        session = Mock()
        session.request.return_value = FakeResponse(payload={"items": []})
        clock = Mock(side_effect=[100.0, 100.25, 101.35])
        sleep = Mock()
        client = RecruiterClient(
            api_key="secret",
            session=session,
            min_request_interval_seconds=1.1,
            clock=clock,
            sleep=sleep,
        )

        client.get_accounts()
        client.get_accounts()

        sleep.assert_called_once()
        self.assertAlmostEqual(sleep.call_args.args[0], 0.85)
        self.assertEqual(session.request.call_count, 2)

    def test_v2_cli_defaults_to_provider_safe_request_pacing(self):
        args = build_parser().parse_args(["accounts"])
        self.assertEqual(args.min_request_interval_seconds, 1.1)

    def test_v2_search_puts_account_in_path(self):
        session = Mock()
        session.request.return_value = FakeResponse(payload={"items": []})
        client = RecruiterClient(api_key="secret", session=session)
        client.search_people("acc_123", {"keywords": "FDE"})
        call = session.request.call_args
        self.assertIn("/v2/acc_123/linkedin/recruiter/search/people", call.args[1])
        self.assertEqual(call.kwargs["json"], {"keywords": "FDE"})

    def test_v2_applicants_use_project_post_read_contract(self):
        session = Mock()
        session.request.return_value = FakeResponse(payload={"data": []})
        client = RecruiterClient(api_key="secret", session=session)
        client.list_project_applicants(
            "acc_123", "project-1", {"sort_by": "NEWEST_FIRST"}, limit=100
        )
        call = session.request.call_args
        self.assertEqual(call.args[0], "POST")
        self.assertIn(
            "/v2/acc_123/linkedin/recruiter/projects/project-1/talent-pool/applicants",
            call.args[1],
        )
        self.assertEqual(call.kwargs["params"], {"limit": 100})
        self.assertEqual(call.kwargs["json"], {"sort_by": "NEWEST_FIRST"})

    def test_v1_applicants_use_job_get_and_cursor(self):
        session = Mock()
        session.request.return_value = FakeResponse(
            payload={"items": [], "total_count": 0}
        )
        client = V1RecruiterClient(
            api_key="secret", base_url="https://api1.unipile.com:13111", session=session
        )
        client.list_job_applicants(
            "legacy-account", "job-1", cursor="page-2", limit=250
        )
        call = session.request.call_args
        self.assertEqual(call.args[0], "GET")
        self.assertTrue(call.args[1].endswith("/api/v1/linkedin/jobs/job-1/applicants"))
        self.assertEqual(
            call.kwargs["params"],
            {
                "account_id": "legacy-account",
                "service": "RECRUITER",
                "limit": 250,
                "cursor": "page-2",
            },
        )

    def test_v1_client_rejects_all_mutation_methods(self):
        client = V1RecruiterClient(
            api_key="secret", base_url="https://api1.unipile.com:13111"
        )
        with self.assertRaisesRegex(ValueError, "read-only"):
            client.direct_request("POST", "/api/v1/linkedin/projects")

    def test_recruiter_resolution_uses_v2_classic_id(self):
        client = RecruiterClient(api_key="secret")
        client.get_profile = Mock(
            side_effect=[
                UnipileAPIError(404, "api/resource_not_found", "not found"),
                {"id": "ACoA-classic-id", "public_identifier": "ada"},
                {"id": "AE-recruiter-id", "is_open_to_work": True},
            ]
        )

        profile, calls = client.resolve_recruiter_profile("acc_123", "ada")

        self.assertEqual(calls, 2)
        self.assertEqual(profile["id"], "AE-recruiter-id")
        self.assertEqual(
            client.get_profile.call_args_list[2].args,
            ("acc_123", "ACoA-classic-id", "recruiter"),
        )

    def test_recruiter_resolution_bridges_live_invalid_public_slug_response(self):
        client = RecruiterClient(api_key="secret")
        client.get_profile = Mock(
            side_effect=[
                UnipileAPIError(
                    400,
                    "api/invalid_parameters",
                    "Invalid User ID.",
                ),
                {"id": "ACoA-classic-id", "public_identifier": "luke-atkins"},
                {"id": "AE-recruiter-id", "is_open_to_work": False},
            ]
        )

        profile, calls = client.resolve_recruiter_profile("acc_123", "luke-atkins")

        self.assertEqual(calls, 2)
        self.assertEqual(profile["id"], "AE-recruiter-id")
        self.assertEqual(
            client.get_profile.call_args_list[1].args,
            ("acc_123", "luke-atkins", "classic"),
        )

    def test_recruiter_profile_url_resolves_directly_to_embedded_candidate_id(self):
        client = RecruiterClient(api_key="secret")
        client.get_profile = Mock(
            return_value={"id": "AE-recruiter-id", "is_open_to_work": True}
        )

        result = client.open_to_work(
            "acc_123",
            "https://www.linkedin.com/talent/profile/AE-recruiter-id?searchRequestId=1",
        )

        self.assertTrue(result["is_open_to_work"])
        self.assertEqual(result["requested_identifier"], "AE-recruiter-id")
        self.assertEqual(result["provider_id"], "AE-recruiter-id")
        self.assertEqual(result["profile_calls"], 1)
        self.assertEqual(
            client.get_profile.call_args.args,
            ("acc_123", "AE-recruiter-id", "recruiter"),
        )

    def test_open_to_work_reads_linkedin_specifics(self):
        client = RecruiterClient(api_key="secret")
        client.get_profile = Mock(
            return_value={
                "id": "AE-recruiter-id",
                "specifics": {"is_open_to_work": True},
            }
        )

        result = client.open_to_work("acc_123", "AE-recruiter-id")

        self.assertTrue(result["is_open_to_work"])
        self.assertEqual(result["profile_calls"], 1)
        self.assertEqual(result["recruiter_search_calls"], 0)
        self.assertEqual(result["is_open_to_work_source"], "profile")

    @staticmethod
    def _profile_with_missing_signal():
        return {
            "id": "AE-recruiter-id",
            "first_name": "Ada",
            "last_name": "Lovelace",
            "specifics": {"is_open_to_work": None},
            "work_experience": [
                {
                    "role": "Engineer",
                    "ended_on": None,
                    "company": {"id": "company-1", "name": "Analytical Engines"},
                }
            ],
        }

    def test_open_to_work_uses_exact_spotlight_positive_when_profile_is_null(self):
        client = RecruiterClient(api_key="secret")
        client.get_profile = Mock(return_value=self._profile_with_missing_signal())
        client.search_people = Mock(
            side_effect=[
                {"data": [{"id": "AE-recruiter-id"}], "total_count": 1},
                {"data": [{"id": "AE-recruiter-id"}], "total_count": 1},
            ]
        )

        result = client.open_to_work("acc_123", "AE-recruiter-id")

        self.assertTrue(result["is_open_to_work"])
        self.assertEqual(result["is_open_to_work_source"], "recruiter_search_spotlight")
        self.assertEqual(result["search_fallback_status"], "exact_positive")
        self.assertEqual(result["recruiter_search_calls"], 2)
        self.assertEqual(
            client.search_people.call_args_list[0].args[1],
            {
                "first_name": ["Ada"],
                "last_name": ["Lovelace"],
                "current_company": [{"id": "company-1", "priority": "MUST_HAVE"}],
                "job_title": [
                    {
                        "name": "Engineer",
                        "priority": "MUST_HAVE",
                        "preferences": "CURRENT",
                    }
                ],
            },
        )
        self.assertEqual(
            client.search_people.call_args_list[1].args[1]["spotlights"],
            ["OPEN_TO_WORK"],
        )

    def test_open_to_work_uses_complete_exact_search_for_negative(self):
        client = RecruiterClient(api_key="secret")
        client.get_profile = Mock(return_value=self._profile_with_missing_signal())
        client.search_people = Mock(
            side_effect=[
                {"data": [{"id": "AE-recruiter-id"}], "total_count": 1},
                {"data": [], "total_count": 0},
            ]
        )

        result = client.open_to_work("acc_123", "AE-recruiter-id")

        self.assertFalse(result["is_open_to_work"])
        self.assertEqual(result["search_fallback_status"], "exact_complete_negative")
        self.assertTrue(result["search_fallback_evidence"]["base_complete"])
        self.assertTrue(result["search_fallback_evidence"]["spotlight_complete"])

    def test_open_to_work_keeps_incomplete_negative_unknown(self):
        client = RecruiterClient(api_key="secret")
        client.get_profile = Mock(return_value=self._profile_with_missing_signal())
        client.search_people = Mock(
            side_effect=[
                {
                    "data": [{"id": "AE-recruiter-id"}],
                    "total_count": 2,
                    "next_cursor": "base-next",
                },
                {"data": [], "total_count": 1, "next_cursor": "spotlight-next"},
            ]
        )

        result = client.open_to_work("acc_123", "AE-recruiter-id")

        self.assertIsNone(result["is_open_to_work"])
        self.assertEqual(result["search_fallback_status"], "incomplete_search")

    def test_open_to_work_keeps_missing_identity_filters_unknown(self):
        client = RecruiterClient(api_key="secret")
        client.get_profile = Mock(
            return_value={
                "id": "AE-recruiter-id",
                "first_name": None,
                "last_name": None,
                "specifics": {"is_open_to_work": None},
                "work_experience": [],
            }
        )
        client.search_people = Mock()

        result = client.open_to_work("acc_123", "AE-recruiter-id")

        self.assertIsNone(result["is_open_to_work"])
        self.assertEqual(result["search_fallback_status"], "unavailable_identity_filters")
        client.search_people.assert_not_called()

    def test_open_to_work_can_build_exact_search_from_display_name(self):
        profile = self._profile_with_missing_signal()
        profile.pop("first_name")
        profile.pop("last_name")
        profile["display_name"] = "Ada Byron Lovelace"
        body = RecruiterClient._exact_identity_search_body(profile)

        self.assertEqual(body["first_name"], ["Ada"])
        self.assertEqual(body["last_name"], ["Lovelace"])
        self.assertEqual(body["current_company"][0]["id"], "company-1")

    def test_open_to_work_refines_name_search_from_exact_base_result(self):
        client = RecruiterClient(api_key="secret")
        client.get_profile = Mock(
            return_value={
                "id": "AE-recruiter-id",
                "display_name": "Ada Lovelace",
                "specifics": {"is_open_to_work": None},
                "work_experience": [],
            }
        )
        exact_search_result = {
            "id": "AE-recruiter-id",
            "display_name": "Ada Lovelace",
            "work_experience": [
                {
                    "role": "Engineer",
                    "ended_on": None,
                    "company": {"id": "company-1"},
                }
            ],
        }
        client.search_people = Mock(
            side_effect=[
                {"data": [exact_search_result], "total_count": 10},
                {"data": [exact_search_result], "total_count": 1},
                {"data": [], "total_count": 0},
            ]
        )

        result = client.open_to_work("acc_123", "AE-recruiter-id")

        self.assertFalse(result["is_open_to_work"])
        self.assertEqual(result["recruiter_search_calls"], 3)
        self.assertEqual(result["search_fallback_status"], "exact_complete_negative")
        self.assertEqual(
            client.search_people.call_args_list[1].args[1]["current_company"],
            [{"id": "company-1", "priority": "MUST_HAVE"}],
        )

    def test_recruiter_search_parameters_use_post_body_contract(self):
        session = Mock()
        session.request.return_value = FakeResponse(payload={"data": []})
        client = RecruiterClient(api_key="secret", session=session)
        client.list_search_parameters(
            "acc_123", "LOCATION", "San Francisco", source="SEARCH", limit=25
        )
        call = session.request.call_args
        self.assertEqual(call.args[0], "POST")
        self.assertIn(
            "/v2/acc_123/linkedin/recruiter/search/parameters", call.args[1]
        )
        self.assertEqual(
            call.kwargs["json"],
            {"source": "SEARCH", "type": "LOCATION", "keywords": "San Francisco"},
        )
        self.assertEqual(call.kwargs["params"], {"limit": 25})

    def test_pipeline_candidate_lookup_follows_cursor_pages(self):
        client = RecruiterClient(api_key="secret")
        client.get_profile = Mock(
            return_value={"first_name": "Ada", "last_name": "Lovelace"}
        )
        client.list_pipeline = Mock(
            side_effect=[
                {"items": [{"candidate_id": "someone-else"}], "cursor": "page-2"},
                {"items": [{"candidate_id": "candidate-123"}]},
            ]
        )

        result = client.find_candidate_in_pipeline(
            "acc_123", "project-1", "candidate-123"
        )

        self.assertEqual(result, {"candidate_id": "candidate-123"})
        self.assertEqual(client.list_pipeline.call_count, 2)
        self.assertIsNone(client.list_pipeline.call_args_list[0].kwargs["cursor"])
        self.assertEqual(
            client.list_pipeline.call_args_list[1].kwargs["cursor"], "page-2"
        )

    def test_save_is_dry_run_by_default(self):
        parser = build_parser()
        args = parser.parse_args([
            "--account-id", "acc_123",
            "save", "candidate-123", "--project", "p1", "--stage", "stage-1",
            "--skip-project-check",
            "--skip-duplicate-check",
        ])
        original = os.environ.get("UNIPILE_V2_API_KEY")
        os.environ["UNIPILE_V2_API_KEY"] = "secret"
        try:
            result = execute(args)
        finally:
            if original is None:
                os.environ.pop("UNIPILE_V2_API_KEY", None)
            else:
                os.environ["UNIPILE_V2_API_KEY"] = original
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["execute_with"]["confirm"], "SAVE:p1:candidate-123")

    def test_cli_defaults_to_v2_and_can_explicitly_select_v1(self):
        parser = build_parser()
        self.assertEqual(parser.parse_args(["accounts"]).backend, "v2")
        self.assertEqual(
            parser.parse_args(["--backend", "v1", "accounts"]).backend, "v1"
        )

    def test_identifier_plan_is_offline_and_credential_independent(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "convert-identifier",
                "https://www.linkedin.com/talent/profile/AE-candidate",
                "--plan-only",
            ]
        )
        original = os.environ.pop("UNIPILE_V2_API_KEY", None)
        try:
            result = execute(args)
        finally:
            if original is not None:
                os.environ["UNIPILE_V2_API_KEY"] = original

        self.assertEqual(result["normalized_identifier"], "AE-candidate")
        self.assertEqual(result["v2_request"]["query"]["variant"], "linkedin_recruiter")

    def test_cli_blocks_v1_mutation_before_loading_credentials(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "--backend",
                "v1",
                "save",
                "candidate-123",
                "--project",
                "p1",
                "--stage",
                "stage-1",
            ]
        )
        with self.assertRaisesRegex(ValueError, "read-only v1 audit backend"):
            execute(args)

    def test_cli_v1_request_blocks_post_before_loading_credentials(self):
        parser = build_parser()
        args = parser.parse_args(
            ["--backend", "v1", "request", "POST", "/api/v1/linkedin/projects"]
        )
        with self.assertRaisesRegex(ValueError, "read-only"):
            execute(args)


if __name__ == "__main__":
    unittest.main()
