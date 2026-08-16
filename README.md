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

You'll need an Unipile v2 API key. You can obtain it from the v2 dashboard.

### Environment Variables
- `UNIPILE_V2_API_KEY`: Your Unipile v2 application key
- `UNIPILE_V2_BASE_URL`: Optional; defaults to `https://api.unipile.com`
- `UNIPILE_V2_LINKEDIN_ACCOUNT_ID`: Optional Recruiter account pin (`acc_...`)

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
LinkedIn Recruiter sourcing workflows using only Unipile v2.

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
unipile-recruiter open-to-work linkedin-public-slug
unipile-recruiter search --body search.json --limit 25
unipile-recruiter search-parameters LOCATION --keywords London
unipile-recruiter pipeline PROJECT_ID --body '{"spotlights":["OPEN_TO_WORK"]}'
```

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

### v1 deprecation

The supported server and CLI no longer read `UNIPILE_DSN`,
`UNIPILE_API_KEY`, `UNIPILE_V1_*`, or any dual-routing configuration. There is
no automatic fallback to v1. The historical dual bridge remains only as
unreferenced migration evidence; its gateway constructors fail closed.

## License

This project is licensed under the MIT License. 
