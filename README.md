# Unipile MCP Server

MCP server for using Unipile to access messages across multiple messaging platforms.

## Overview

A Model Context Protocol (MCP) server implementation that provides integration with the Unipile messaging platform. This server enables AI models to interact with messages from various messaging platforms (Mobile, Mail, WhatsApp, LinkedIn, Slack, Twitter, Telegram, Instagram, Messenger) through a standardized interface.

For more information about the Model Context Protocol and how it works, see [Anthropic's MCP documentation](https://www.anthropic.com/news/model-context-protocol).

## Unipile Subscription

To use the Unipile services, a subscription is required. I am not paid by Unipile to do this; I am simply a user who loves using Unipile because it works effectively. For more details on the subscription and features, visit the [Unipile Messaging API page](https://www.unipile.com/communication-api/messaging-api/).

## Communication Capabilities

With Unipile, you can communicate seamlessly across a wide range of social platforms. This includes popular messaging services such as:

- **LinkedIn**: Engage with professional contacts, send messages, and manage your LinkedIn interactions directly through the Unipile interface.
- **WhatsApp**: Send and receive messages, manage chats, and stay connected with your contacts.
- **Instagram**: Interact with followers, respond to direct messages, and manage your Instagram communications.
- **Messenger**: Communicate with friends and family through Facebook Messenger.
- **Telegram**: Access your Telegram chats and messages effortlessly.

Unipile's integration with these platforms allows for a unified communication experience, making it easier to manage interactions across different services. This is particularly beneficial for users who rely on LinkedIn for professional networking, as it enables them to leverage AI capabilities, such as Claude, to enhance their communication strategies.

## Components

### Resources

The server exposes the following resources:

* `unipile://messages`: A dynamic resource that provides access to messages from connected messaging platforms

### Example Prompts

- Get all messages from a chat:
    ```
    Get all messages from chat ID "chat_123"
    ```

### Tools

The server offers several tools for accessing Unipile data:

#### Message Management Tools
* `unipile_get_chat_messages`
  * Retrieve all messages from a specific chat with pagination support
  * Input: chat_id (required), batch_size (optional, default: 100)
  * Returns: Array of message objects

## Setup

You'll need an Unipile v2 API key for production use. An explicitly selected
legacy v1 connection can also be configured for read-only migration audits.

### Environment Variables
- `UNIPILE_V2_API_KEY`: Your Unipile v2 application key
- `UNIPILE_V2_BASE_URL`: Optional; defaults to `https://api.unipile.com`
- `UNIPILE_V2_LINKEDIN_ACCOUNT_ID`: Optional Recruiter account pin (`acc_...`)
- `UNIPILE_V1_API_KEY`: Legacy audit key (v1 reads only)
- `UNIPILE_V1_BASE_URL`: Legacy v1 DSN/base URL
- `UNIPILE_V1_LINKEDIN_ACCOUNT_ID`: Optional legacy account pin
- `UNIPILE_RECRUITER_BACKEND`: Optional CLI default; `v2` unless explicitly set

Note: Keep your API key secure and never commit it to version control.

### Docker Installation

You can either build the image locally or pull it from Docker Hub. The image is built for the Linux platform.

#### Supported Platforms
- Linux/amd64
- Linux/arm64
- Linux/arm/v7

#### Option 1: Pull from Docker Hub
```bash
docker pull buryhuang/mcp-unipile:latest
```

#### Option 2: Build Locally
```bash
docker build -t mcp-unipile .
```

Run the container:
```bash
docker run \
  -e UNIPILE_V2_API_KEY=your_api_key_here \
  buryhuang/mcp-unipile:latest
```

## Cross-Platform Publishing

To publish the Docker image for multiple platforms, you can use the `docker buildx` command. Follow these steps:

1. **Create a new builder instance** (if you haven't already):
   ```bash
   docker buildx create --use
   ```

2. **Build and push the image for multiple platforms**:
   ```bash
   docker buildx build --platform linux/amd64,linux/arm64,linux/arm/v7 -t buryhuang/mcp-unipile:latest --push .
   ```

3. **Verify the image is available for the specified platforms**:
   ```bash
   docker buildx imagetools inspect buryhuang/mcp-unipile:latest
   ```

## Usage with Claude Desktop

### Docker Usage
```json
{
  "mcpServers": {
    "unipile": {
      "command": "docker",
      "args": [
        "run",
        "-i",
        "--rm",
        "-e",
        "UNIPILE_V2_API_KEY=your_api_key_here",
        "buryhuang/mcp-unipile:latest"
      ]
    }
  }
}
```

## Development

To set up the development environment:

```bash
pip install -e .
```

## LinkedIn Recruiter CLI

The package also installs `unipile-recruiter`, a JSON-first CLI for guarded
LinkedIn Recruiter workflows with explicit Unipile v1/v2 read selection. V2 is
the default and the only backend that can mutate Recruiter. There is no
automatic fallback between versions.

Set credentials in environment variables; the API key is intentionally not
accepted as a command-line argument:

```sh
export UNIPILE_V2_API_KEY="..."
export UNIPILE_V2_LINKEDIN_ACCOUNT_ID="acc_..."  # optional when one account is running
```

Read-only examples:

```sh
unipile-recruiter doctor
unipile-recruiter projects --keywords Strala
unipile-recruiter project 2107551666
unipile-recruiter convert-identifier 'https://www.linkedin.com/talent/profile/AE...' --plan-only
unipile-recruiter convert-identifier 'https://www.linkedin.com/in/public-slug'
unipile-recruiter open-to-work linkedin-public-slug
unipile-recruiter open-to-work 'https://www.linkedin.com/talent/profile/AE...'
unipile-recruiter search --body search.json --limit 25
unipile-recruiter search-parameters LOCATION --keywords London
unipile-recruiter pipeline PROJECT_ID --body '{"spotlights":["OPEN_TO_WORK"]}'
unipile-recruiter applicants V2_PROJECT_ID --limit 100
unipile-recruiter --backend v1 applicants V1_JOB_ID --limit 250
```

`applicants` deliberately takes a V2 project ID on V2 and a V1 job ID on V1.
The CLI never translates or reuses identifiers across versions. V1 requires
explicit selection with `--backend v1` (or `UNIPILE_RECRUITER_BACKEND=v1`) and
accepts only `accounts`, `doctor`, `projects`, `project`, `applicants`, and
read-only `request` commands.

Profile commands accept a provider-issued ID, a public `/in/` URL or slug, or
a Recruiter candidate profile URL. Recruiter profile links are resolved to the
candidate ID embedded in the URL and the canonical ID returned by Unipile;
Recruiter search-results URLs remain inputs to `search-url`, not profile calls.
`convert-identifier --plan-only` emits the typed input classification and exact
v2 request shape without credentials or a network call. Without `--plan-only`,
it resolves the profile and returns the canonical Recruiter `provider_id`,
public identifier, profile variant, and provider-call count.

V2 CLI calls enforce a 1.1-second minimum interval between consecutive provider
requests, including account discovery, Classic-to-Recruiter identity bridging,
and Open-to-Work search fallback. Batch operators can raise the interval with
`--min-request-interval-seconds` or `UNIPILE_V2_MIN_REQUEST_INTERVAL_SECONDS`;
the CLI never retries a provider `429` automatically.

Mutation commands are dry-runs by default. A candidate save first validates the
project and prints the exact confirmation token:

```sh
unipile-recruiter save CANDIDATE_ID \
  --project PROJECT_ID --stage PIPELINE_STAGE_ID
```

Only the explicit second invocation mutates Recruiter:

```sh
unipile-recruiter save CANDIDATE_ID \
  --project PROJECT_ID --stage PIPELINE_STAGE_ID \
  --execute --confirm 'SAVE:PROJECT_ID:CANDIDATE_ID'
```

`--stage` is the exact pipeline stage ID. Project creation and editing are
guarded convenience commands. `proxy` exposes
Unipile's raw LinkedIn gateway; embedded `POST`, `PUT`, `PATCH`, and `DELETE`
requests also require an execution flag and exact confirmation token.

Run `unipile-recruiter capabilities` for the supported surface and
`python -m unittest discover -s tests` for the safety/unit tests.

### v1 migration boundary

V1 is supported only as an explicitly selected, read-only historical audit
backend. It never receives writes and is never used when a V2 call fails. Keep
version-specific account, project, job, stage, profile, and candidate IDs
separate. LinkedIn may reject concurrent Recruiter sessions with
`errors/multiple_sessions`; stop and repair the connection rather than routing
around that warning.

## License

This project is licensed under the MIT License. 

## V2 outreach: one implementation for CLI and MCP

The CLI and the `unipile_recruiter` MCP tool share the request builder in
`outreach.py` and the paced V2 transport. LinkedIn chat lists always use an inbox;
`/{account_id}/chats` is not the LinkedIn listing endpoint.

### Install and authenticate

Python 3.11+ is required. From this checkout, install or update both entry points:

```sh
uv tool install --force --reinstall .
unipile-recruiter capabilities
unipile-recruiter endpoint-map
```

Set **`UNIPILE_V2_SERVICE_API_KEY`** in your secret manager for all V2 operations.
`UNIPILE_V2_API_KEY` remains a compatibility fallback. Keys must belong to the
same Unipile Application as the accounts. No key is accepted on the command line.
Service keys can administer webhooks; Account keys cannot. The supported account
and inbox reads can still work with an Account key, so a successful account read
alone does not prove webhook permissions.

On Richard's macOS host, the existing MCP credential is in Keychain under service
`unipile-v2-api-key`, account `codex`. Select that same key explicitly:

```sh
unipile-recruiter --keychain doctor --outreach
```

`--keychain` overrides environment credentials for this invocation. It does not
copy or print the secret. Elsewhere, inject the service key as an environment
variable. MCP server startup accepts the same environment variables. The existing
macOS launcher continues to work with its Keychain credential.

### Read and track

Put global options before the command. `--account-id acc_...` is optional when
exactly one healthy LinkedIn account can be discovered. Examples below assume
the service key is injected; add `--keychain` before the command on the local host.

```sh
unipile-recruiter doctor --outreach
unipile-recruiter inboxes
unipile-recruiter chats --inbox-id CLASSIC_PRIMARY --limit 20
unipile-recruiter chats --inbox-id RECRUITER_PRIMARY --limit 20
unipile-recruiter messages CHAT_ID --limit 20
unipile-recruiter connections --limit 20
unipile-recruiter invitations --type sent --limit 20 --offset 0
unipile-recruiter webhooks
```

These commands return a **single bounded page**, including the provider's
`next_cursor`. Pass it back with `--cursor`; invitation lists use `--offset`
instead. A page is not a complete inbox or connection history. Maximum requested
page size is 100. A disappeared invitation does not prove acceptance: reconcile
with connections. Connection acceptance does not mean recruiting interest.

`doctor --outreach` probes account status, credits, inboxes, Classic and Recruiter
chat lists, invitations, connections and webhook administration. It does not
send anything or verify callback delivery. An unhealthy result exits with code 2.
It stops on 401, 403 or 429; no automatic credential fallback or write retry occurs.
Transport timeouts also return structured errors. A write timeout is an uncertain
outcome: inspect the destination before deciding whether to retry.

### Preview invitations and messages

```sh
unipile-recruiter invite ACo_CLASSIC_ID --text 'Connection note'
unipile-recruiter chat-start AE_RECRUITER_ID \
  --inbox-id RECRUITER_PRIMARY --subject 'Role conversation' \
  --signature 'Richard' --text 'Reviewed message text'
unipile-recruiter chat-start ACo_CLASSIC_ID \
  --inbox-id CLASSIC_PRIMARY --text 'Reviewed message text'
unipile-recruiter message-send CHAT_ID --text 'Reviewed reply'
```

Each write returns its method, path, body and `execute_with` values. To send,
repeat the exact command with `--execute --confirm 'TOKEN_FROM_PREVIEW'`.
The new outreach tokens are bound to the account, destination and content;
changing any of those requires a new preview. These confirmations do not grant
consent or implement campaign suppression. The caller must check prior outreach,
replies and do-not-contact status before sending. This CLI is not a bulk scheduler.

Recruiter starts use `specifics.linkedin.recruiter.subject` and `.signature`,
and `users_ids` is one **string** for an individual conversation. These fields
come from the current endpoint schema, which supersedes older guide examples
using `options` and a single-element array. Use the existing chat ID for replies.
Invitations require a Classic `ACo...` identity; do not substitute a Recruiter ID.

### Webhook administration

```sh
unipile-recruiter webhook-create --body webhook.json
unipile-recruiter webhook-update we_ENDPOINT_ID --body '{"description":"Updated description"}'
unipile-recruiter webhook-delete we_ENDPOINT_ID
```

Example `webhook.json` (replace the destination and account before execution):

```json
{
  "url": "https://your-service.example/unipile/events",
  "account_ids": ["acc_YOUR_ACCOUNT"],
  "trigger_events": ["message.new", "relation.new"]
}
```

Create, update and delete all require preview confirmation. The API validates event
names. Omitting `account_ids` subscribes at application scope, so prefer the target
account explicitly. Signing secrets are redacted from CLI/MCP responses; retrieve
the endpoint secret securely through the Unipile Dashboard. Validate incoming
`unipile-signature` using that endpoint secret and the raw body, not the API key.
This package manages registrations; it does not host the webhook receiver.

### MCP use

The MCP exposes `unipile_recruiter` with an `args` array, passed to the same CLI
parser **without a shell**:

```json
{"args":["capabilities"]}
{"args":["doctor","--outreach"]}
{"args":["chats","--inbox-id","RECRUITER_PRIMARY","--limit","10"]}
{"args":["--account-id","acc_YOUR_ACCOUNT","invite","ACo_CLASSIC_ID","--text","Reviewed note"]}
```

After reviewing the preview, repeat the arguments with `--execute` and `--confirm`
if the send is authorized. Connection settings and pacing are fixed at MCP startup;
MCP cannot switch to V1 or replace the credential through a tool call. Use inline
JSON for `--body` in MCP; stdin (`-`) and local file inputs are disallowed.
Raw `request` and `proxy` commands remain CLI-only; MCP uses the named operations.

The existing `unipile_get_recent_messages` tool now accepts `inbox_id` (default
`RECRUITER_PRIMARY`). Its result is a bounded envelope with per-chat message pages
and cursors, preserving sender metadata for response tracking. `batch_size` limits
both chat count and messages per chat to at most 20. This envelope replaces the old
flat, unbounded message list. Use `unipile_recruiter` for explicit page traversal.

### Endpoint map and verification

| Command | Method and V2 path | Minimum key |
| --- | --- | --- |
| inboxes | GET `/{account_id}/inboxes` | Account |
| chats | GET `/{account_id}/inboxes/{inbox_id}/chats` | Account |
| messages | GET `/{account_id}/chats/{chat_id}/messages` | Account |
| connections | GET `/{account_id}/users/me/relations` | Account |
| invitations / invite | GET / POST `/{account_id}/users/me/relation-requests` | Account |
| chat-start | POST `/{account_id}/inboxes/{inbox_id}/chats/send` | Account |
| message-send | POST `/{account_id}/chats/{chat_id}/messages/send` | Account |
| webhooks / webhook-create | GET / POST `/webhooks/endpoints/` | Service |
| webhook-update / webhook-delete | PATCH / DELETE `/webhooks/endpoints/{endpoint_id}` | Service |

Service keys cover both columns. `endpoint-map` emits the same mapping as JSON.
Read routes were smoke-tested on 5 September 2026. Write payloads, previews and
confirmation guards are tested locally; **no candidate-facing send or webhook
mutation was performed during development**. A controlled send/reply/delivery
exercise is still required before launching a campaign. Credits are a current
balance, not permission to send at that rate.

References: [V2 keys](https://developer.unipile.com/v2.0/docs/api-keys),
[chat starts](https://developer.unipile.com/v2.0/reference/startchatfrominbox),
[invitations](https://developer.unipile.com/v2.0/docs/linkedin-manage-invitations),
[webhook verification](https://developer.unipile.com/v2.0/docs/configure-a-webhook).

Run `python -m unittest discover -s tests` for the offline contract and regression
suite. Use `doctor --outreach` for bounded live reads; its results contain no
message bodies or signing secrets.
