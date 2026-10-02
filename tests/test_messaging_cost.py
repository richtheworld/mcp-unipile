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

class NativeMessagingCostTests(unittest.TestCase):
    @staticmethod
    def response(identifier='AEexample', cost=0, allowed=True):
        urn = 'urn:li:ts_profile:' + identifier
        return {'data': {'data': {'recipientInMailCostInfoByRecipients': {'elements': [{
            'entityUrn': 'urn:li:ts_recipient_inmail_cost_info:' + urn,
            'recipient': {'entityUrn': urn, 'contactInfo': {'primaryEmail': 'private@example.test'}},
            'inMailCost': cost, 'canAcceptInMails': allowed,
            'messageSendEligibility': {'emailInitialSendEligibility': {'eligible': False}},
        }]}}}}

    def test_native_quote_does_not_need_open_profile_flag_and_omits_contacts(self):
        from mcp_server_unipile.messaging_cost import parse_native
        for cost, status in [(0, 'free'), (1, 'requires_credit')]:
            result = parse_native(self.response(cost=cost), ['AEexample'])['AEexample']
            self.assertEqual(result['status'], status)
            self.assertEqual(result['expected_inmail_credits'], cost)
            self.assertFalse(result['email_initial_send_eligible'])
            self.assertNotIn('private@example.test', str(result))
            self.assertNotIn('recipient', result)
        result = parse_native(self.response(allowed=False), ['AEexample'])['AEexample']
        self.assertEqual(result['status'], 'unavailable')
        self.assertIsNone(result['expected_inmail_credits'])

    def test_native_exact_recipient_coverage_and_typed_cost(self):
        from mcp_server_unipile.messaging_cost import parse_native
        for recipients in [['AEother'], ['AEexample', 'AEmissing']]:
            with self.assertRaises(ValueError):
                parse_native(self.response(), recipients)
        response = self.response()
        elements = response['data']['data']['recipientInMailCostInfoByRecipients']['elements']
        elements.append(elements[0])
        with self.assertRaises(ValueError):
            parse_native(response, ['AEexample'])
        for cost in [True, '0', -1, 2, None]:
            self.assertEqual(parse_native(self.response(cost=cost), ['AEexample'])['AEexample']['status'], 'unknown')

    def test_native_request_is_get_and_rejects_injected_or_duplicate_ids(self):
        from mcp_server_unipile.messaging_cost import native_request
        body = native_request('123', '456', ['AEexample'])
        self.assertEqual(body['method'], 'GET')
        self.assertNotIn('action=execute', str(body))
        self.assertIn('urn%3Ali%3Ats_contract%3A456', body['query_params']['variables'])
        for recipients in [[], ['AEexample'] * 2, ['ACoWrong'], ['AEabc),other:bad'], ['AE' + str(i) for i in range(26)]]:
            with self.assertRaises(ValueError):
                native_request('123', '456', recipients)
        with self.assertRaises(ValueError):
            native_request('123,other', '456', ['AEexample'])

    def test_cli_native_project_quote_matches_selected_contract(self):
        client = RecruiterClient('test', session=Mock())
        previous = client.min_request_interval_seconds
        client.get_linkedin_contracts = Mock(return_value={'contracts': [{'product': 'recruiter', 'selected': True, 'id': 'RECRUITER_456'}]})
        client.get_project = Mock(return_value={'id': '123'})
        client.get_profile = Mock()
        client.proxy_request = Mock(return_value=self.response(cost=1))
        args = build_parser().parse_args(['--account-id', 'acc_test', 'messaging-cost', 'AEexample', '--project-id', '123', '--contract-id', '456'])
        result = execute(args, client)
        self.assertEqual(result['expected_inmail_credits'], 1)
        client.get_profile.assert_not_called()
        self.assertEqual(client.proxy_request.call_args.args[1]['method'], 'GET')
        self.assertEqual(client.min_request_interval_seconds, previous)
        args.contract_id = '999'
        client.proxy_request.reset_mock()
        with self.assertRaises(ValueError):
            execute(args, client)
        client.proxy_request.assert_not_called()
        self.assertEqual(client.min_request_interval_seconds, previous)

    def test_cli_normalizes_resolved_recruiter_profile_url(self):
        client = RecruiterClient('test', session=Mock())
        client.get_linkedin_contracts = Mock(return_value={'contracts': [{'product': 'recruiter', 'selected': True, 'id': 'RECRUITER_456'}]})
        client.get_project = Mock(return_value={'id': '123'})
        client.resolve_recruiter_profile = Mock(return_value=({'id': 'https://www.linkedin.com/talent/profile/AEexample?project=123'}, 2))
        client.proxy_request = Mock(return_value=self.response(cost=0))
        args = build_parser().parse_args(['--account-id', 'acc_test', 'messaging-cost', 'https://www.linkedin.com/in/example', '--project-id', '123'])
        result = execute(args, client)
        self.assertEqual(result['expected_inmail_credits'], 0)
        self.assertIn('urn%3Ali%3Ats_profile%3AAEexample', client.proxy_request.call_args.args[1]['query_params']['variables'])
        client.resolve_recruiter_profile.return_value = ({'id': None}, 2)
        client.proxy_request.reset_mock()
        with self.assertRaises(ValueError):
            execute(args, client)
        client.proxy_request.assert_not_called()
