"""Shared V2 outreach requests for CLI and MCP; no shell or automatic retries."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import urlparse, quote

from .recruiter_client import RecruiterClient, UnipileAPIError

# CLI / MCP -> validated request -> content-bound preview -> paced V2 transport.
ENDPOINTS = {
    'inboxes': ('GET', '/v2/{account_id}/inboxes'),
    'chats': ('GET', '/v2/{account_id}/inboxes/{inbox_id}/chats'),
    'messages': ('GET', '/v2/{account_id}/chats/{chat_id}/messages'),
    'connections': ('GET', '/v2/{account_id}/users/me/relations'),
    'invitations': ('GET', '/v2/{account_id}/users/me/relation-requests'),
    'invite': ('POST', '/v2/{account_id}/users/me/relation-requests'),
    'chat-start': ('POST', '/v2/{account_id}/inboxes/{inbox_id}/chats/send'),
    'message-send': ('POST', '/v2/{account_id}/chats/{chat_id}/messages/send'),
    'webhooks': ('GET', '/v2/webhooks/endpoints/'),
    'webhook-create': ('POST', '/v2/webhooks/endpoints/'),
    'webhook-update': ('PATCH', '/v2/webhooks/endpoints/{endpoint_id}'),
    'webhook-delete': ('DELETE', '/v2/webhooks/endpoints/{endpoint_id}'),
}


def endpoint_map() -> dict[str, Any]:
    return {name: {'method': method, 'path': path,
                   'required_key': 'service' if name.startswith('webhook') else 'account or service',
                   'verification': 'contract tested; live sends not performed' if method != 'GET' else 'read smoke tested 2026-09-05',
                   'docs': 'https://developer.unipile.com/v2.0/reference'}
            for name, (method, path) in ENDPOINTS.items()}


def segment(value: Any, name: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_\-:.=]+', value) or value in ('.', '..'):
        raise ValueError(f'{name} must be a provider-issued ID without URL delimiters')
    return value


def nonempty(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} must be non-empty text')
    return value


def redact(value: Any) -> Any:
    """Webhook responses contain signing secrets. Keep them out of CLI/MCP output."""
    if isinstance(value, dict):
        return {k: ('[REDACTED]' if any(s in k.lower() for s in ('secret', 'api_key', 'token')) else redact(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def request_for(command: str, account_id: str | None, options: dict[str, Any]) -> dict[str, Any]:
    method, path = ENDPOINTS[command]
    fields = dict(options)
    if '{account_id}' in path:
        fields['account_id'] = segment(account_id, 'account_id')
        if not fields['account_id'].startswith('acc_'):
            raise ValueError('V2 account_id must start with acc_')
    fields.setdefault('inbox_id', 'RECRUITER_PRIMARY')
    for field in re.findall(r'{(\w+)}', path):
        fields[field] = segment(fields.get(field), field)
    path = path.format(**{key: quote(value, safe='') for key, value in fields.items() if isinstance(value, str)})
    params: dict[str, Any] = {}
    body: dict[str, Any] | None = None
    if method == 'GET':
        limit = fields.get('limit', 20)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError('limit must be between 1 and 100')
        params['limit'] = limit
        if fields.get('cursor'):
            if command in ('invitations', 'webhooks'):
                raise ValueError('Invitations and webhooks use offset pagination, not cursor')
            params['cursor'] = fields['cursor']
        if fields.get('offset') is not None:
            if command not in ('invitations', 'webhooks'):
                raise ValueError('Use cursor pagination for this command')
            offset = fields['offset']
            if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
                raise ValueError('offset must be a non-negative integer')
            if fields.get('cursor'):
                raise ValueError('Use cursor or offset, not both')
            params['offset'] = offset
        if command == 'invitations':
            kind = fields.get('type', 'sent')
            if kind not in ('sent', 'received'):
                raise ValueError('Invitation type must be sent or received')
            params['type'] = kind
    elif command == 'invite':
        user_id = segment(fields.get('user_id'), 'user_id')
        if not user_id.startswith('ACo'):
            raise ValueError('Invitations require the Classic ACo user ID, not a Recruiter ID')
        body = {'user_id': user_id}
        if fields.get('text'):
            body['message'] = fields['text']
    elif command in ('chat-start', 'message-send'):
        body = {'text': nonempty(fields.get('text'), 'text')}
        if command == 'chat-start':
            body['users_ids'] = segment(fields.get('user_id'), 'user_id')
            inbox = fields['inbox_id']
            if inbox.startswith('RECRUITER_') and inbox.endswith('_PRIMARY'):
                if not body['users_ids'].startswith('AE'):
                    raise ValueError('Recruiter chats require a canonical AE Recruiter user ID; use convert-identifier first')
                body['specifics'] = {'linkedin': {'recruiter': {
                    'subject': nonempty(fields.get('subject'), 'subject'),
                    'signature': nonempty(fields.get('signature'), 'signature'),
                }}}
            elif inbox == 'CLASSIC_PRIMARY':
                if not body['users_ids'].startswith('ACo'):
                    raise ValueError('Classic chats require a Classic ACo user ID')
            else:
                raise ValueError('Start chats in CLASSIC_PRIMARY or RECRUITER_*_PRIMARY')
    elif command in ('webhook-create', 'webhook-update'):
        body = fields.get('body')
        if not isinstance(body, dict) or not body:
            raise ValueError('Webhook body must be a non-empty JSON object')
        allowed = {'url', 'description', 'trigger_events', 'account_ids'}
        if set(body) - allowed:
            raise ValueError('Webhook body supports url, description, trigger_events, account_ids')
        if command == 'webhook-create' and not {'url', 'trigger_events'} <= set(body):
            raise ValueError('Webhook creation requires url and trigger_events')
        if 'url' in body:
            url = urlparse(nonempty(body['url'], 'url'))
            if url.scheme != 'https' or not url.hostname or url.username or url.password:
                raise ValueError('Webhook URL must be HTTPS without embedded credentials')
        if 'trigger_events' in body and (not isinstance(body['trigger_events'], list) or not body['trigger_events'] or any(not isinstance(e, str) or not e for e in body['trigger_events'])):
            raise ValueError('trigger_events must be a non-empty list of event names')
        if 'account_ids' in body:
            if not isinstance(body['account_ids'], list):
                raise ValueError('account_ids must be a list')
            for aid in body['account_ids']:
                if not segment(aid, 'account_id').startswith('acc_'):
                    raise ValueError('account_ids must contain V2 acc_ IDs')
    return {'method': method, 'path': path, 'params': params, 'body': body}


def run(client: RecruiterClient, command: str, account_id: str | None, options: dict[str, Any]) -> Any:
    request = request_for(command, account_id, options)
    if request['method'] != 'GET':
        digest = hashlib.sha256(json.dumps(request, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:24]
        token = f'{command}:{digest}'
        if not options.get('execute'):
            return {'dry_run': True, 'operation': command, 'request': request,
                    'execute_with': {'execute': True, 'confirm': token}}
        if options.get('confirm') != token:
            raise ValueError('Confirmation must match this exact account, destination and content; preview again')
    result = client.direct_request(**request)
    return redact(result) if command.startswith('webhook') else result


def doctor(client: RecruiterClient, account_id: str) -> dict[str, Any]:
    """Bounded, read-only probes. Stop on credential/provider pushback."""
    checks: dict[str, Any] = {}
    accounts = client.get_accounts()
    account = next((a for a in accounts if a.get('id') == account_id), None)
    if account is None:
        return {'ok': False, 'checks': {'account': {'ok': False, 'detail': 'Account not accessible'}}}
    checks['account'] = {'ok': account.get('status') == 'running', 'status': account.get('status'),
                         'products': account.get('metadata', {}).get('products_connection_status', {})}
    probes = [('credits', lambda: client.get_inmail_credits(account_id)),
              ('inboxes', lambda: run(client, 'inboxes', account_id, {'limit': 1})),
              ('classic_chats', lambda: run(client, 'chats', account_id, {'inbox_id': 'CLASSIC_PRIMARY', 'limit': 1})),
              ('recruiter_chats', lambda: run(client, 'chats', account_id, {'inbox_id': 'RECRUITER_PRIMARY', 'limit': 1})),
              ('invitations', lambda: run(client, 'invitations', account_id, {'limit': 1})),
              ('connections', lambda: run(client, 'connections', account_id, {'limit': 1})),
              ('webhooks', lambda: run(client, 'webhooks', None, {'limit': 1}))]
    for name, probe in probes:
        try:
            result = probe()
            checks[name] = {'ok': True}
            if name == 'credits':
                checks[name]['credits'] = result.get('credits')
        except UnipileAPIError as error:
            checks[name] = {'ok': False, 'error': error.as_dict()}
            if error.status_code in (401, 403, 429):
                break
    return {'ok': all(c['ok'] for c in checks.values()) and len(checks) == len(probes) + 1,
            'api_version': 'v2', 'checks': checks,
            'writes_tested': False, 'webhook_delivery_tested': False}
