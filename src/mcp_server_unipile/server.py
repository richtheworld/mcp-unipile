import os
import json
import logging
from typing import Any, Optional
from mcp.server.models import InitializationOptions
import mcp.types as types
from mcp.server import NotificationOptions, Server
import mcp.server.stdio
from pydantic import AnyUrl
import re
import contextlib
import io
from markdownify import markdownify

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from .unipile_client import UnipileClient, get_linkedin_profile_field
from .recruiter_client import normalize_profile_identifier, RecruiterClient, UnipileAPIError
from . import outreach
from .recruiter_cli import build_parser, execute

class UnipileWrapper:
    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None):
        base_url = base_url or os.getenv(
            "UNIPILE_V2_BASE_URL", "https://api.unipile.com"
        )
        api_key = api_key or os.getenv("UNIPILE_V2_SERVICE_API_KEY") or os.getenv("UNIPILE_V2_API_KEY")

        logger.debug(f"Using API key: {'[MASKED]' if api_key else 'None'}")
        if not api_key:
            raise ValueError("UNIPILE_V2_API_KEY environment variable is required")

        self.client = UnipileClient(api_key=api_key, base_url=base_url)
        self.recruiter = RecruiterClient(api_key=api_key, base_url=base_url,
            min_request_interval_seconds=max(1.1, float(os.getenv("UNIPILE_V2_MIN_REQUEST_INTERVAL_SECONDS", "1.1"))))

    def recruiter_command(self, argv: list[str]) -> Any:
        """Execute CLI arguments in process, using one persistent paced V2 client."""
        if not isinstance(argv, list) or any(not isinstance(a, str) for a in argv):
            raise ValueError("args must be an array of CLI argument strings")
        if any(a == "-" for a in argv):
            raise ValueError("MCP cannot read CLI JSON from stdin; pass an inline JSON object")
        output = io.StringIO()
        try:
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                args = build_parser().parse_args(["--backend", "v2", *argv])
        except SystemExit as error:
            if error.code == 0:
                return {"help": output.getvalue()}
            raise ValueError(output.getvalue().strip()) from None
        if args.backend != "v2" or args.keychain or args.base_url is not None or args.min_request_interval_seconds is not None:
            raise ValueError("MCP connection and pacing settings are fixed at startup; V2 only")
        supported = set(outreach.ENDPOINTS) | {
            "capabilities", "endpoint-map", "doctor", "accounts", "projects", "project",
            "applicants", "profile", "open-to-work", "convert-identifier", "search",
            "search-url", "search-parameters", "pipeline", "inmail-credits",
        }
        if args.command not in supported:
            raise ValueError("Command is CLI-only; MCP supports named outreach operations and sourcing reads")
        for field in ("body", "params"):
            value = getattr(args, field, None)
            if value is not None and not value.lstrip().startswith("{"):
                raise ValueError("MCP JSON inputs must be inline objects, not stdin or local files")
        return execute(args, client_override=self.recruiter)

    def recent_messages(self, account_id: str, inbox_id: str, limit: int) -> dict[str, Any]:
        page = outreach.run(self.recruiter, "chats", account_id,
                            {"inbox_id": inbox_id, "limit": limit})
        items = page.get("data") or page.get("items") or []
        chats = []
        for chat in items[:limit]:
            messages = outreach.run(self.recruiter, "messages", account_id,
                                   {"chat_id": chat["id"], "limit": limit})
            chats.append({"chat_id": chat["id"], "messages": messages})
        return {"inbox_id": inbox_id, "chats": chats, "next_cursor": page.get("next_cursor"),
                "bounded_sample": True}

    def _extract_person_info(self, original_data: dict) -> dict:
        """Extract core person information from message data"""
        try:
            person_info = {}
            if "conversation" in original_data:
                participants = original_data["conversation"].get("conversationParticipants", [])
                for participant in participants:
                    if "participantType" in participant and "member" in participant["participantType"]:
                        member = participant["participantType"]["member"]
                        if member.get("firstName") and isinstance(member["firstName"], dict):
                            first_name = member["firstName"].get("text", "")
                            last_name = member["lastName"].get("text", "") if member.get("lastName") else ""
                            headline = member.get("headline", {}).get("text", "") if member.get("headline") else ""
                            pronoun = member.get("pronoun", {}).get("standardizedPronoun", "") if member.get("pronoun") else ""
                            
                            person_info[participant["backendUrn"]] = {
                                "name": f"{first_name} {last_name}".strip(),
                                "headline": headline,
                                "pronoun": pronoun
                            }
            return person_info
        except Exception as e:
            logger.error(f"Error extracting person info: {str(e)}")
            return {}

    def _extract_core_message(self, message: dict) -> dict:
        """Extract core message content and metadata"""
        try:
            # Extract basic message info
            core_message = {
                "id": message.get("id", ""),
                "text": message.get("text", ""),
                "timestamp": message.get("timestamp", ""),
                "sender_id": message.get("sender_id", ""),
                "chat_info": message.get("chat_info", {})
            }

            # Extract person information if original data exists
            if "original" in message:
                try:
                    original_data = json.loads(message["original"])
                    person_info = self._extract_person_info(original_data)
                    if person_info:
                        core_message["participants"] = person_info
                except json.JSONDecodeError:
                    pass

            return core_message
        except Exception as e:
            logger.error(f"Error extracting core message: {str(e)}")
            return message

    def _extract_core_email(self, email: dict) -> dict:
        """Extract core email content and metadata"""
        try:
            # Extract basic email info
            core_email = {
                "id": email.get("id", ""),
                "subject": email.get("subject", ""),
                "date": email.get("date", ""),
                "role": email.get("role", ""),
                "folders": email.get("folders", []),
                "has_attachments": email.get("has_attachments", False)
            }

            # Convert body_plain to markdown if available
            if email.get("kind") in ["1_meta", "2_full"]:
                body_plain = email.get("body_plain", "")
                if body_plain:
                    # Convert text to markdown using markdownify and remove URLs
                    markdown_text = markdownify(body_plain)
                    # Remove URLs using regex - matches http/https/ftp URLs
                    markdown_text = re.sub(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', '', markdown_text)
                    # Remove any leftover empty brackets
                    markdown_text = re.sub(r'\[\s*\]', '', markdown_text)
                    core_email["body_markdown"] = markdown_text

            # Add sender and recipients
            if "from_attendee" in email:
                core_email["from"] = email["from_attendee"].get("display_name", "")
            
            core_email["to"] = [att.get("display_name", "") for att in email.get("to_attendees", [])]
            core_email["cc"] = [att.get("display_name", "") for att in email.get("cc_attendees", [])]

            # Add attachment info
            if email.get("attachments"):
                core_email["attachments"] = [{
                    "name": att.get("name", ""),
                    "size": att.get("size", 0),
                    "type": att.get("mime", "")
                } for att in email["attachments"]]

            return core_email
        except Exception as e:
            logger.error(f"Error extracting core email: {str(e)}")
            return email

    def get_emails(self, account_id: str, limit: int = 10) -> str:
        """Get emails for a specific account"""
        try:
            emails = self.client.get_emails(account_id=account_id, limit=limit)
            # Transform each email to extract core content
            core_emails = [self._extract_core_email(email) for email in emails]
            return json.dumps(core_emails)
        except Exception as e:
            logger.error(f"Error getting emails: {str(e)}")
            return json.dumps({"error": str(e)})

    def get_accounts(self) -> str:
        """Get all connected accounts"""
        try:
            accounts = self.client.get_accounts()
            return json.dumps(accounts, default=str)
        except Exception as e:
            logger.error(f"Error getting accounts: {str(e)}")
            return json.dumps({"error": str(e)})

    def get_linkedin_open_to_work(
        self,
        account_id: str,
        identifier: str,
        linkedin_api: str = "recruiter",
    ) -> str:
        """Return the documented LinkedIn Open to Work signal without contact data."""
        try:
            input_reference = identifier
            requested_identifier = normalize_profile_identifier(identifier)
            identifier = requested_identifier
            classic_profile = None

            # Recruiter profile requests require LinkedIn's internal member ID.
            # Resolve normal public slugs first so callers can pass sheet URLs/slugs.
            recruiter_id_prefixes = ("ACoA", "AEMA", "AEM", "AE")
            if linkedin_api == "recruiter" and not (
                identifier.startswith(recruiter_id_prefixes) or identifier.isdigit()
            ):
                classic_profile = self.client.get_linkedin_profile(
                    account_id=account_id,
                    identifier=identifier,
                    linkedin_api=None,
                )
                provider_id = classic_profile.get("provider_id") or classic_profile.get("id")
                if not provider_id:
                    raise ValueError("LinkedIn profile did not provide an internal member ID")
                identifier = str(provider_id)

            profile = self.client.get_linkedin_profile(
                account_id=account_id,
                identifier=identifier,
                linkedin_api=linkedin_api,
            )
            classic_signal = (
                get_linkedin_profile_field(classic_profile, "is_open_to_work")
                if classic_profile is not None
                else None
            )
            recruiter_signal = get_linkedin_profile_field(
                profile, "is_open_to_work"
            )
            result = {
                "provider": profile.get("provider"),
                "provider_id": profile.get("provider_id") or profile.get("id"),
                "public_identifier": profile.get("public_identifier"),
                "first_name": profile.get("first_name"),
                "last_name": profile.get("last_name"),
                "input_reference": input_reference,
                "requested_identifier": requested_identifier,
                "linkedin_api": linkedin_api,
                "is_open_to_work": recruiter_signal,
                "classic_is_open_to_work": classic_signal,
                "is_recruiter_only_open_to_work": (
                    recruiter_signal is True and classic_signal is not True
                    if classic_profile is not None
                    else None
                ),
                "is_open_profile": get_linkedin_profile_field(
                    profile, "is_open_profile"
                ),
            }
            return json.dumps(result, default=str)
        except Exception as e:
            logger.error(f"Error getting LinkedIn Open to Work status: {str(e)}")
            return json.dumps({"error": str(e)})

    def get_chats(self, account_id: str, limit: int = 10) -> str:
        """Get all available chats for a specific account"""
        try:
            chats = self.client.get_chats(account_id=account_id, limit=limit)
            return json.dumps(chats)
        except Exception as e:
            logger.error(f"Error getting chats: {str(e)}")
            return json.dumps({"error": str(e)})

    def get_chat_messages(
        self, account_id: str, chat_id: str, batch_size: int = 100
    ) -> str:
        """Get all messages from a chat"""
        try:
            messages = self.client.get_messages_as_list(
                account_id, chat_id, batch_size
            )
            # Transform each message to extract core content
            core_messages = [self._extract_core_message(msg) for msg in messages]
            return json.dumps(core_messages)
        except Exception as e:
            logger.error(f"Error getting chat messages: {str(e)}")
            return json.dumps({"error": str(e)})

    def get_all_messages(self, account_id: str, limit: int = 10) -> str:
        """Get messages from all available chats for a specific account"""
        try:
            # First get all chats for this account
            chats = json.loads(self.get_chats(account_id, limit))
            if isinstance(chats, dict) and "error" in chats:
                return json.dumps(chats)
                
            all_messages = []
            
            # Then get messages from each chat
            for chat in chats:
                chat_id = chat.get('id')
                if chat_id:
                    messages = self.client.get_messages_as_list(account_id, chat_id)
                    # Transform each message to extract core content
                    core_messages = [self._extract_core_message(msg) for msg in messages]
                    all_messages.extend(core_messages)
            
            return json.dumps(all_messages, default=str)
        except Exception as e:
            logger.error(f"Error getting all messages: {str(e)}")
            return json.dumps({"error": str(e)})

async def main(base_url: Optional[str] = None, api_key: Optional[str] = None):
    """Run the Unipile MCP server."""
    logger.info("Server starting")
    unipile = UnipileWrapper(base_url, api_key)
    server = Server("unipile")

    @server.list_resources()
    async def handle_list_resources() -> list[types.Resource]:
        return [
            types.Resource(
                uri=AnyUrl("unipile://accounts"),
                name="Unipile Accounts",
                description="List of connected messaging accounts from supported platforms: Mobile, Mail, WhatsApp, LinkedIn, Slack, Twitter, Telegram, Instagram, Messenger",
                mimeType="application/json",
            ),
        ]

    @server.read_resource()
    async def handle_read_resource(uri: AnyUrl) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
        if uri.scheme != "unipile":
            raise ValueError(f"Unsupported URI scheme: {uri.scheme}")

        path = str(uri).replace("unipile://", "")
        try:
            if path == "accounts":
                return unipile.get_accounts()
            else:
                raise ValueError(f"Unknown resource path: {path}")
        except Exception as e:
            logger.error(f"Error reading resource {path}: {str(e)}")
            return [types.TextContent(
                type="text",
                text=json.dumps({"error": str(e)}),
                mimeType="application/json",
                uri=uri
            )]

    @server.list_tools()
    async def handle_list_tools() -> list[types.Tool]:
        """List available tools"""
        return [
            types.Tool(
                name="unipile_recruiter",
                description="Use the shared V2 CLI in process. Start with args=[\"capabilities\"] or [\"endpoint-map\"]. Commands include doctor --outreach, inboxes, chats, messages, connections, invitations, invite, chat-start, message-send, webhooks and webhook-create/update/delete; also sourcing commands search, profile, projects, pipeline and open-to-work. Writes return exact previews unless --execute and the matching --confirm are supplied. No shell invocation. Connection settings fixed at startup.",
                inputSchema={"type": "object", "properties": {"args": {"type": "array", "items": {"type": "string"}}}, "required": ["args"]},
            ),
            types.Tool(
                name="unipile_get_accounts",
                description="Get all connected messaging accounts from supported platforms: Mobile, Mail, WhatsApp, LinkedIn, Slack, Twitter, Telegram, Instagram, Messenger. Returns account details including connection parameters, ID, name, creation date, signatures, groups, and sources.",
                inputSchema={
                    "type": "object",
                    "properties": {},
                },
            ),
            types.Tool(
                name="unipile_get_recent_messages",
                description="Get recent messages from all chats associated with a specific account. Supports messages from: Mobile, Mail, WhatsApp, LinkedIn, Slack, Twitter, Telegram, Instagram, Messenger. Returns message details including text content, sender info, timestamps, attachments, reactions, quoted messages, and metadata.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "account_id": {"type": "string", "description": "Connected Unipile v2 account ID (acc_...)."},
                        "batch_size": {"type": "integer", "minimum": 1, "maximum": 20, "description": "Bounded maximum chats and messages per chat (default: 10)"},
                        "inbox_id": {"type": "string", "default": "RECRUITER_PRIMARY", "description": "CLASSIC_PRIMARY or RECRUITER_PRIMARY"}
                    },
                    "required": ["account_id"]
                },
            ),
            types.Tool(
                name="unipile_get_linkedin_open_to_work",
                description="Retrieve a LinkedIn profile through a connected Recruiter contract and return the documented is_open_to_work signal. Accepts a provider ID, public LinkedIn profile URL/slug, or LinkedIn Recruiter profile URL. The response is deliberately limited to identity and availability fields and excludes contact details.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "account_id": {"type": "string", "description": "Connected Unipile LinkedIn account ID"},
                        "identifier": {"type": "string", "description": "LinkedIn public profile/slug, Recruiter profile URL, or provider-internal profile ID"},
                        "linkedin_api": {
                            "type": "string",
                            "enum": ["recruiter", "sales_navigator"],
                            "default": "recruiter",
                            "description": "LinkedIn paid product used to retrieve the profile"
                        }
                    },
                    "required": ["account_id", "identifier"]
                },
            ),
            types.Tool(
                name="unipile_get_emails",
                description="Get recent emails from a specific account. Returns email details including subject, body, sender, recipients, attachments, and metadata.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "account_id": {"type": "string", "description": "The ID of the account to get emails from"},
                        "limit": {"type": "integer", "description": "Maximum number of emails to return (default: 10)"}
                    },
                    "required": ["account_id"]
                },
            ),
        ]

    @server.call_tool()
    async def handle_call_tool(
        name: str, arguments: dict[str, Any] | None
    ) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
        """Handle tool execution requests"""
        try:
            if name == "unipile_recruiter":
                result = unipile.recruiter_command((arguments or {}).get("args", []))
                return [types.TextContent(type="text", text=json.dumps(result, default=str))]
            if name == "unipile_get_accounts":
                results = unipile.get_accounts()
                return [types.TextContent(
                    type="text",
                    text=results,
                    mimeType="application/json",
                    uri=AnyUrl("unipile://accounts")
                )]
            elif name == "unipile_get_recent_messages":
                if not arguments:
                    raise ValueError("Missing arguments for get_recent_messages")
                
                account_id = arguments["account_id"]
                batch_size = arguments.get("batch_size", 10)
                
                if not isinstance(batch_size, int) or isinstance(batch_size, bool) or not 1 <= batch_size <= 20:
                    raise ValueError("batch_size must be between 1 and 20")
                results = unipile.recent_messages(account_id, arguments.get("inbox_id", "RECRUITER_PRIMARY"), batch_size)
                return [types.TextContent(type="text", text=json.dumps(results),
                                          mimeType="application/json", uri=AnyUrl(f"unipile://messages/{account_id}"))]
            elif name == "unipile_get_linkedin_open_to_work":
                if not arguments:
                    raise ValueError("Missing arguments for unipile_get_linkedin_open_to_work")

                account_id = arguments["account_id"]
                identifier = arguments["identifier"]
                resource_identifier = normalize_profile_identifier(identifier)
                linkedin_api = arguments.get("linkedin_api", "recruiter")
                if linkedin_api == "recruiter":
                    result = unipile.recruiter.open_to_work(account_id, identifier)
                else:
                    profile = unipile.recruiter.get_profile(account_id, identifier, linkedin_api)
                    result = {"provider_id": profile.get("provider_id") or profile.get("id"),
                              "is_open_to_work": get_linkedin_profile_field(profile, "is_open_to_work")}
                results = json.dumps(result, default=str)
                return [types.TextContent(
                    type="text",
                    text=results,
                    mimeType="application/json",
                    uri=AnyUrl(f"unipile://linkedin/open-to-work/{resource_identifier}")
                )]
            elif name == "unipile_get_emails":
                if not arguments:
                    raise ValueError("Missing arguments for get_emails")
                
                account_id = arguments["account_id"]
                limit = arguments.get("limit", 10)
                
                results = unipile.get_emails(account_id=account_id, limit=limit)
                return [types.TextContent(
                    type="text",
                    text=results,
                    mimeType="application/json",
                    uri=AnyUrl(f"unipile://emails/{account_id}")
                )]
            else:
                raise ValueError(f"Unknown tool: {name}")

        except UnipileAPIError as e:
            return types.CallToolResult(isError=True, content=[types.TextContent(type="text", text=json.dumps({"error": e.as_dict()}))])
        except Exception as e:
            logger.error("Tool %s failed (%s)", name, type(e).__name__)
            return types.CallToolResult(isError=True, content=[types.TextContent(type="text", text=json.dumps({"error": str(e)}))])

    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        logger.info("Server running with stdio transport")
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="unipile",
                server_version="0.1.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
