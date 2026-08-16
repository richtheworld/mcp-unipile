import json
import os
import unittest
from unittest.mock import Mock

from mcp_server_unipile.recruiter_cli import build_parser, execute
from mcp_server_unipile.recruiter_client import (
    RecruiterClient,
    UnipileAPIError,
    normalize_base_url,
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

    def test_v2_search_puts_account_in_path(self):
        session = Mock()
        session.request.return_value = FakeResponse(payload={"items": []})
        client = RecruiterClient(api_key="secret", session=session)
        client.search_people("acc_123", {"keywords": "FDE"})
        call = session.request.call_args
        self.assertIn("/v2/acc_123/linkedin/recruiter/search/people", call.args[1])
        self.assertEqual(call.kwargs["json"], {"keywords": "FDE"})

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


if __name__ == "__main__":
    unittest.main()
