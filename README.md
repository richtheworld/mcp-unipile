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
unipile-recruiter messaging-cost 'https://www.linkedin.com/talent/profile/AE...'
unipile-recruiter projects --keywords Strala
unipile-recruiter project 2107551666
unipile-recruiter convert-identifier 'https://www.linkedin.com/talent/profile/AE...' --plan-only
unipile-recruiter convert-identifier 'https://www.linkedin.com/in/public-slug'
unipile-recruiter open-to-work linkedin-public-slug
unipile-recruiter open-to-work 'https://www.linkedin.com/talent/profile/AE...'
unipile-recruiter search --body search.json --limit 25
unipile-recruiter search-parameters LOCATION --keywords London
unipile-recruiter pipeline PROJECT_ID --contract-id CONTRACT_ID --limit 25 --offset 0
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
`next_cursor`. Pass it back with `--cursor`; invitation and webhook lists use `--offset`
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
Raw `request`/`proxy` and project/pipeline mutations (`project-create`, `project-edit`,
`save`) remain CLI-only; MCP allowlists the named outreach commands and sourcing reads.

The existing `unipile_get_recent_messages` tool now accepts `inbox_id`. When omitted, it selects Recruiter if running,
otherwise Classic for LinkedIn, and preserves the generic chat route for other
messaging providers. Its result is a bounded envelope with per-chat message pages
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

### Reconcile native Recruiter pipeline profiles (read-only)

The `pipeline` command now uses the verified native route, replacing its failing
standard wrapper. Its pagination uses `--offset` and `--limit` (1–100):

```sh
unipile-recruiter --backend v2 --keychain pipeline PROJECT_ID \
  --contract-id CONTRACT_ID --limit 25 --offset 0
```

The response contains `items`, `paging`, `page_valid` and `next_offset`. Only a
valid page beginning at offset zero, containing the entire total, and agreeing
with a fresh project stage-count read can claim `inventory_complete`. Legacy `--cursor` and nonempty `--body` filters are rejected
before any API request; they are not silently discarded. Invalid pages and failed
whole-inventory stage checks exit with code 2; a valid partial page exits 0 and
provides its `next_offset`. For stable whole-project
inventory and identity suggestions, use the command below.

When the standard pipeline wrapper fails or omits profile sections, use the native
pipeline search through the V2 Magic route:

```sh
unipile-recruiter --backend v2 --keychain pipeline-reconcile PROJECT_ID \
  --contract-id CONTRACT_ID --max-passes 3 > reconciliation.json
```

Use the verified numeric Recruiter project ID. Both native pipeline commands
resolve the single selected Recruiter contract automatically; optional
`--contract-id` must be numeric and match that selected contract. Missing,
ambiguous or malformed selected contracts stop before the project read. No
contract is selected or changed by these commands. The reconciliation command reads the
project name and stage counts, then pages `/talent/search/api/talentRecruiterSearchHits`
with `q=pipelineSearch` and an explicit professional-profile decoration. It enforces
at least five seconds after every provider response, including account discovery.
The native request shape was verified on 1 October 2026; native LinkedIn contracts
can change. No V1 fallback or LinkedIn mutation is performed.

`inventory_complete: true` requires **two consecutive equal complete snapshots**,
including exact paging, unique candidate entity IDs, verified project/contract
membership on native identifiers, and agreement with project stage counts. The default limit is three passes of at most 100 pages each (100
records per page); use `--max-pages` to change the bound. A single pass can have
`snapshot_complete: true` but cannot establish a stable inventory. Exit code 2
indicates an incomplete or unstable inventory. A provider error ends the loop and
preserves the last complete snapshot, when available, with its age and pass proof.

Records are classified as `linked_with_sections`, `linked_sparse`,
`imported_unlinked`, `unknown` or `resolution_error`. Section counts describe only
what the provider returned; they do not prove a full profile. The output contains
professional employment/education/skills, stages, duplicate suggestions and a
`next_actions` queue. Name-only collisions remain unresolved. Exact normalized
name, company and title overlap can suggest an imported-to-linked mapping; records
are never merged. No contacts, private notes, resumes, messages or proxy headers
are exported.

An optional evidence file lets a separate browser or Fiber investigation contribute
professional identity evidence. This command does not call Fiber or automate a
browser itself:

```json
{
  "schema_version": 1,
  "matches": [
    {
      "candidate_id": "EXACT_INVENTORY_ENTITY_URN_OR_RECRUITER_ID",
      "public_profile_url": "https://www.linkedin.com/in/example-person",
      "source": "fiber",
      "match_status": "confirmed",
      "confidence": 0.95,
      "name": "Ada Example",
      "company": "Example Engines",
      "title": "Engineer"
    }
  ]
}
```

Pass it with `--evidence evidence.json`. Allowed sources are `fiber`, `browser`,
`linkedin_api`, `public_web`; allowed match statuses are `confirmed`, `possible`, `unresolved`.
Optional `rationale` preserves up to 1,000 characters of professional identity
reasoning, and `source_urls` preserves up to five HTTPS provenance URLs of at most
2,000 characters each, without credentials or custom ports. Unresolved entries
may omit the URL. `matched_recruiter_id` optionally references
an exact linked record in the inventory and must agree with its public URL.
Unsupported fields, unsafe URLs and absent/ambiguous target IDs are rejected.
A caller's `confirmed` flag alone cannot confirm an identity: corroborating
professional fields are required, conflicting profile URLs remain ambiguous,
and every mapping is a suggestion for review. This command neither links nor
removes imported Recruiter records.

### Archive unlinked Recruiter records

`pipeline-archive-unlinked` removes explicitly unlinked imported records from a
project's active pipeline by moving them to its verified **Archived** stage.
Linked profiles (including sparse profiles), anonymized/unknown records, and
already archived records are preserved. This does not permanently delete data.

```sh
unipile-recruiter --backend v2 --keychain pipeline-archive-unlinked PROJECT_ID \
  --plan-file ./archive-plan.json

# Inspect the exact targets, then use the confirmation token from the dry run.
unipile-recruiter --backend v2 --keychain pipeline-archive-unlinked PROJECT_ID \
  --plan-file ./archive-plan.json --execute --confirm 'ARCHIVE_UNLINKED:PROJECT_ID:HASH'
```

The local manifest contains candidate identifiers and names; keep it private.
Execution re-reads the entire project, validates stage counts, recreates the
request, and rejects a changed inventory, edited manifest, or wrong token. A
single native batch contains at most 100 targets. Calls use Unipile v2 with at
least five seconds after each completed request; there is no v1 fallback.
Readback must prove every target is archived and every retained record is
unchanged before the command reports verified success. Failed or uncertain
writes are never automatically retried: inspect a fresh inventory first.

The operation uses Recruiter's native `talentHiringProjectCandidates` batch
GraphQL mutation through Unipile's raw LinkedIn route. The native mutation and
payload were verified live on 2026-10-01. The ordinary candidate-save endpoint
returned 403 for archiving imported records and is not used for this command.
Native query IDs can change; failure stops the operation rather than guessing
another route. Archiving imports does not add replacement LinkedIn profiles:
reconcile and save any needed linked replacements first.

### Candidate messaging credit estimate

`messaging-cost IDENTIFIER` reads the candidate in the connected account's Recruiter
context and returns `status` (`free`, `requires_credit`, `unknown`, or `unavailable`),
`expected_inmail_credits` (0, 1, or null), the relevant profile signals and a UTC
check time. It uses no sending endpoint and omits candidate contact details.

Open Profile and first-degree connections imply zero credits. A reachable
non-connection with an explicitly closed profile implies one credit. Missing or
invalid fields remain unknown; Open to Work is not a free-message signal.
`can_send_inmail=false` is unavailable unless the recipient is a first-degree
connection, which can receive ordinary connection messages without InMail.
This estimate applies to initial contact, not follow-ups or existing chats. It
does not calculate currency cost or check the credit balance (`inmail-credits`
is separate); free sends remain subject to provider limits and sometimes require
a positive credit balance. Profile reads are paced at least five seconds apart.

Sources: [LinkedIn message types](https://www.linkedin.com/help/linkedin/answer/a417258)
and [Unipile provider limits](https://developer.unipile.com/docs/provider-limits-and-restrictions).
