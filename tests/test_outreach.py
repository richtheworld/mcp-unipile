import json
import os
import unittest
from unittest.mock import Mock, patch

import requests
from mcp_server_unipile import outreach
from mcp_server_unipile.recruiter_client import RecruiterClient, UnipileAPIError
from mcp_server_unipile.recruiter_cli import build_parser, execute, get_client, main
from mcp_server_unipile.server import UnipileWrapper


class OutreachTests(unittest.TestCase):
    def setUp(self):
        self.client = RecruiterClient('test', session=Mock())
        self.client.direct_request = Mock(return_value={'data': [], 'next_cursor': 'next'})

    def test_linkedin_uses_inbox_chats_and_keeps_pagination(self):
        for inbox in ['CLASSIC_PRIMARY', 'RECRUITER_PRIMARY']:
            page = outreach.run(self.client, 'chats', 'acc_123', {'inbox_id': inbox, 'limit': 2, 'cursor': 'abc'})
            request = self.client.direct_request.call_args.kwargs
            self.assertEqual(request['path'], f'/v2/acc_123/inboxes/{inbox}/chats')
            self.assertEqual(request['params'], {'limit': 2, 'cursor': 'abc'})
            self.assertEqual(page['next_cursor'], 'next')

    def test_padded_provider_chat_ids_are_preserved_and_encoded(self):
        request = outreach.request_for('messages', 'acc_123', {'chat_id': 'chat_base64=='})
        self.assertEqual(request['path'], '/v2/acc_123/chats/chat_base64%3D%3D/messages')

    def test_invitations_use_offset_and_classic_identity(self):
        request = outreach.request_for('invitations', 'acc_123', {'offset': 20})
        self.assertEqual(request['params'], {'limit': 20, 'offset': 20, 'type': 'sent'})
        with self.assertRaises(ValueError):
            outreach.request_for('invitations', 'acc_123', {'cursor': 'wrong'})
        with self.assertRaises(ValueError):
            outreach.request_for('webhooks', None, {'cursor': 'wrong'})
        self.assertEqual(outreach.request_for('webhooks', None, {'offset': 20})['params']['offset'], 20)
        with self.assertRaises(ValueError):
            outreach.request_for('invite', 'acc_123', {'user_id': 'AE-recruiter'})

    def test_recruiter_send_current_schema_and_content_bound_confirmation(self):
        args = {'user_id': 'AE-person', 'text': 'Hello', 'subject': 'Role', 'signature': 'Richard'}
        preview = outreach.run(self.client, 'chat-start', 'acc_123', args)
        self.client.direct_request.assert_not_called()
        body = preview['request']['body']
        self.assertEqual(body, {'users_ids': 'AE-person', 'text': 'Hello',
            'specifics': {'linkedin': {'recruiter': {'subject': 'Role', 'signature': 'Richard'}}}})
        confirmed = {**args, **preview['execute_with']}
        for field, value in [('text', 'Different'), ('user_id', 'AE-other'), ('inbox_id', 'CLASSIC_PRIMARY')]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                outreach.run(self.client, 'chat-start', 'acc_123', {**confirmed, field: value})
        with self.assertRaises(ValueError):
            outreach.run(self.client, 'chat-start', 'acc_other', confirmed)
        self.client.direct_request.assert_not_called()
        outreach.run(self.client, 'chat-start', 'acc_123', confirmed)
        self.client.direct_request.assert_called_once_with(**preview['request'])

    def test_identity_family_must_match_inbox(self):
        for inbox, user in [('CLASSIC_PRIMARY', 'AE-person'), ('RECRUITER_PRIMARY', 'ACo-person')]:
            with self.assertRaises(ValueError):
                outreach.run(self.client, 'chat-start', 'acc_123', {'inbox_id': inbox, 'user_id': user,
                    'text': 'Hi', 'subject': 'Role', 'signature': 'R'})
        self.client.direct_request.assert_not_called()

    def test_all_mutations_preview_and_validate_before_network(self):
        cases = {
            'invite': {'user_id': 'ACo-person'},
            'message-send': {'chat_id': 'chat-1', 'text': 'Reply'},
            'webhook-create': {'body': {'url': 'https://example.com/hook', 'trigger_events': ['message.new']}},
            'webhook-update': {'endpoint_id': 'we_123', 'body': {'description': 'Updated'}},
            'webhook-delete': {'endpoint_id': 'we_123'},
        }
        for command, options in cases.items():
            with self.subTest(command=command):
                self.assertTrue(outreach.run(self.client, command, 'acc_123', options)['dry_run'])
                with self.assertRaises(ValueError):
                    outreach.run(self.client, command, 'acc_123', {**options, 'execute': True})
        self.client.direct_request.assert_not_called()

    def test_input_validation_rejects_path_escape_missing_subject_and_unbounded_reads(self):
        for options in [{'limit': 0}, {'limit': 1000}, {'offset': -1}, {'inbox_id': '../other'}, {'cursor': 'x', 'offset': 0}]:
            with self.assertRaises(ValueError):
                outreach.run(self.client, 'chats', 'acc_123', options)
        for options in [{'user_id': 'AE-person', 'text': 'Hi'}, {'user_id': 'AE-person', 'text': '  ', 'subject': 's', 'signature': 'r'}]:
            with self.assertRaises(ValueError):
                outreach.run(self.client, 'chat-start', 'acc_123', options)
        self.client.direct_request.assert_not_called()

    def test_webhooks_need_no_linkedin_discovery_and_redact_secrets(self):
        args = build_parser().parse_args(['webhooks'])
        self.client.discover_linkedin_account = Mock(side_effect=AssertionError('Not needed'))
        self.client.direct_request.return_value = {'data': [{'secret': 'hidden', 'id': 'we_1'}]}
        self.assertEqual(execute(args, self.client), {'data': [{'secret': '[REDACTED]', 'id': 'we_1'}]})

    def test_mcp_uses_same_executor_and_preserves_recent_messages_fields(self):
        wrapper = UnipileWrapper.__new__(UnipileWrapper)
        wrapper.recruiter = self.client
        argv = ['--account-id', 'acc_123', 'chat-start', 'AE-person', '--text', 'Hi', '--subject', 'Role', '--signature', 'R']
        self.assertEqual(wrapper.recruiter_command(argv), execute(build_parser().parse_args(argv), self.client))
        self.client.direct_request.side_effect = [
            {'data': [{'id': 'chat1'}], 'next_cursor': 'chat-next'},
            {'data': [{'id': 'message1', 'is_sender': False, 'sender_id': 'person'}], 'next_cursor': 'message-next'},
        ]
        result = wrapper.recent_messages('acc_123', 'RECRUITER_PRIMARY', 1)
        self.assertEqual(result['next_cursor'], 'chat-next')
        self.assertFalse(result['chats'][0]['messages']['data'][0]['is_sender'])
        self.assertEqual(result['chats'][0]['messages']['next_cursor'], 'message-next')

    def test_recent_messages_preserves_other_providers_and_classic_only_accounts(self):
        wrapper = UnipileWrapper.__new__(UnipileWrapper)
        wrapper.recruiter = self.client
        for account, expected in [
            ({'provider': 'whatsapp'}, '/v2/acc_123/chats'),
            ({'provider': 'linkedin', 'metadata': {'products_connection_status': {'classic': 'running'}}}, '/v2/acc_123/inboxes/CLASSIC_PRIMARY/chats'),
            ({'provider': 'linkedin', 'metadata': {'products_connection_status': {'recruiter': 'running'}}}, '/v2/acc_123/inboxes/RECRUITER_PRIMARY/chats'),
        ]:
            self.client.direct_request.reset_mock()
            self.client.direct_request.side_effect = [account, {'data': []}]
            wrapper.recent_messages('acc_123', None, 1)
            call = self.client.direct_request.call_args
            path = call.kwargs.get('path') or call.args[1]
            self.assertEqual(path, expected)

    def test_mcp_rejects_connection_override_and_bad_parser_inputs(self):
        wrapper = UnipileWrapper.__new__(UnipileWrapper)
        wrapper.recruiter = self.client
        for args in [['--backend', 'v1', 'accounts'], ['--keychain', 'accounts'], ['unknown'], ['webhook-create', '--body', '-'], ['webhook-create', '--body=-'], ['webhook-create', '--body', '/tmp/private.json'], ['request', 'GET', '/v2/webhooks/endpoints/'], ['proxy', '--body', '{}'], ['project-create', '--body', '{}'], ['project-edit', 'project1', '--body', '{}'], ['save', 'AE-person', '--project', 'p1', '--stage', 's1']]:
            with self.assertRaises(ValueError):
                wrapper.recruiter_command(args)
        self.assertIn('help', wrapper.recruiter_command(['--help']))

    def test_transport_failure_does_not_retry_write(self):
        client = RecruiterClient('test', session=Mock())
        client.session.request.side_effect = requests.Timeout()
        with self.assertRaises(UnipileAPIError) as error:
            client.direct_request('POST', '/v2/acc_123/chats/chat/messages/send', body={'text': 'hello'})
        self.assertEqual(error.exception.error_type, 'transport_error')
        client.session.request.assert_called_once()

    def test_doctor_stops_on_provider_pushback(self):
        self.client.get_accounts = Mock(return_value=[{'id': 'acc_123', 'status': 'running'}])
        self.client.get_inmail_credits = Mock(side_effect=UnipileAPIError(429, 'rate_limit', 'slow down'))
        result = outreach.doctor(self.client, 'acc_123')
        self.assertFalse(result['ok'])
        self.assertEqual(result['checks']['credits']['error']['status_code'], 429)
        self.client.direct_request.assert_not_called()

    def test_service_key_selected_for_all_commands(self):
        with patch.dict(os.environ, {'UNIPILE_V2_SERVICE_API_KEY': 'service', 'UNIPILE_V2_API_KEY': 'account'}, clear=True):
            self.assertEqual(get_client(build_parser().parse_args(['inboxes'])).api_key, 'service')

    def test_keychain_explicit_selection_never_exposes_key_in_args(self):
        with patch('mcp_server_unipile.recruiter_cli.subprocess.run', return_value=Mock(returncode=0, stdout='service-secret\n')) as run:
            client = get_client(build_parser().parse_args(['--keychain', 'webhooks']))
            self.assertEqual(client.api_key, 'service-secret')
            self.assertNotIn('service-secret', str(run.call_args))


if __name__ == '__main__':
    unittest.main()
