import unittest
from unittest.mock import Mock
from mcp_server_unipile.messaging_cost import assess
from mcp_server_unipile.recruiter_cli import build_parser, execute
from mcp_server_unipile.recruiter_client import RecruiterClient


class MessagingCostTests(unittest.TestCase):
    def test_classification(self):
        cases = [
            ({"is_open_profile": True}, "free", 0),
            ({"specifics": {"is_open_profile": True}}, "free", 0),
            ({"network_distance": "FIRST_DEGREE", "can_send_inmail": False}, "free", 0),
            ({"is_open_profile": False, "network_distance": "SECOND_DEGREE", "can_send_inmail": True}, "requires_credit", 1),
            ({"is_open_profile": False, "network_distance": "THIRD_DEGREE", "can_send_inmail": True}, "requires_credit", 1),
            ({"is_open_to_work": True}, "unknown", None),
            ({"is_open_profile": False}, "unknown", None),
            ({"can_send_inmail": False, "is_open_profile": True}, "unavailable", None),
            ({"network_distance": "SELF", "is_open_profile": True}, "unavailable", None),
            ({"is_open_profile": "true", "can_send_inmail": "false"}, "unknown", None),
            ({"specifics": {"is_open_profile": None}, "is_open_profile": True}, "unknown", None),
        ]
        for profile, status, credits in cases:
            with self.subTest(profile=profile):
                result = assess(profile)
                self.assertEqual((result["status"], result["expected_inmail_credits"]), (status, credits))
                self.assertNotIn("first_name", result)

    def test_cli_uses_recruiter_read_and_restores_pacing(self):
        client = RecruiterClient("test", session=Mock())
        client.min_request_interval_seconds = 1.1
        def read(account, identifier):
            self.assertGreaterEqual(client.min_request_interval_seconds, 5)
            self.assertEqual((account, identifier), ("acc_test", "AEexample"))
            return {"specifics": {"is_open_profile": True}, "first_name": "Private"}, 1
        client.resolve_recruiter_profile = Mock(side_effect=read)
        args = build_parser().parse_args(["--account-id", "acc_test", "messaging-cost", "AEexample"])
        result = execute(args, client)
        self.assertEqual(result["expected_inmail_credits"], 0)
        self.assertEqual(result["profile_calls"], 1)
        self.assertEqual(client.min_request_interval_seconds, 1.1)
        client.session.request.assert_not_called()

    def test_provider_error_propagates_without_send_and_restores_pacing(self):
        client = RecruiterClient("test", session=Mock())
        previous = client.min_request_interval_seconds
        client.resolve_recruiter_profile = Mock(side_effect=ValueError("read failed"))
        args = build_parser().parse_args(["--account-id", "acc_test", "messaging-cost", "AEexample"])
        with self.assertRaisesRegex(ValueError, "read failed"):
            execute(args, client)
        self.assertEqual(client.min_request_interval_seconds, previous)
        client.session.request.assert_not_called()
