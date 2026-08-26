import requests
import logging
from typing import Any, List, Dict, Optional, Generator

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.unipile.com"


def get_linkedin_profile_field(profile: Dict[str, Any], field: str) -> Any:
    """Read a LinkedIn-specific profile field from the v2 response shape."""
    specifics = profile.get("specifics")
    if isinstance(specifics, dict) and field in specifics:
        return specifics[field]
    return profile.get(field)


class UnipileClient:
    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL):
        """
        Initialize the Unipile client

        Args:
            base_url: Unipile v2 base URL
            api_key: Your Unipile API key
        """
        self.base_url = base_url.rstrip("/")
        if self.base_url != DEFAULT_BASE_URL:
            raise ValueError("Unipile v2 requires base URL https://api.unipile.com")
        self.headers = {
            'X-API-KEY': api_key,
            'accept': 'application/json'
        }

    @staticmethod
    def _account(account_id: str) -> str:
        if not account_id.startswith("acc_"):
            raise ValueError("Unipile v2 account IDs must start with acc_")
        return account_id

    def get_accounts(self) -> List[Dict]:
        """
        Get all connected accounts
        
        Returns:
            List of account dictionaries from the items array
            
        Raises:
            requests.exceptions.RequestException: If the API request fails
        """
        url = f"{self.base_url}/v2/accounts/"
        response = requests.get(
            url, headers=self.headers, params={"limit": 100}, timeout=60
        )
        response.raise_for_status()
        data = response.json()
        
        return data.get("data") or data.get("items") or []

    def get_linkedin_profile(
        self,
        account_id: str,
        identifier: str,
        linkedin_api: Optional[str] = "recruiter",
    ) -> Dict:
        """Retrieve a LinkedIn profile through Classic, Recruiter, or Sales Navigator."""
        account_id = self._account(account_id)
        url = f"{self.base_url}/v2/{account_id}/users/{identifier}"
        params = {
            "variant": f"linkedin_{linkedin_api}" if linkedin_api else "linkedin_classic"
        }
        response = requests.get(
            url, headers=self.headers, params=params, timeout=60
        )
        response.raise_for_status()
        return response.json()

    def get_chats(self, account_id: str, limit: int = 10) -> List[Dict]:
        """
        Get available chats for a specific account
        
        Args:
            account_id: The ID of the account to get chats from
            limit: Maximum number of chats to return (default: 10)
        
        Returns:
            List of chat dictionaries from the items array. Each chat contains:
            - id: The chat ID
            - account_id: The associated account ID
            - account_type: The type of account (e.g., WHATSAPP, LINKEDIN)
            - provider_id: The provider's chat ID
            - name: Chat name or title
            - type: Chat type
            - timestamp: Last activity timestamp
            - unread_count: Number of unread messages
            - archived: Whether the chat is archived
            - subject: Chat subject or topic
            And more platform-specific fields
            
        Raises:
            requests.exceptions.RequestException: If the API request fails
        """
        account_id = self._account(account_id)
        url = f"{self.base_url}/v2/{account_id}/chats"
        response = requests.get(
            url, headers=self.headers, params={"limit": limit}, timeout=60
        )
        response.raise_for_status()
        data = response.json()
        
        return data.get("data") or data.get("items") or []

    def get_all_messages(
        self,
        account_id: str,
        chat_id: str,
        batch_size: int = 100,
    ) -> Generator[Dict, None, None]:
        """
        Get all messages from a chat using pagination
        
        Args:
            chat_id: The ID of the chat to get messages from
            batch_size: Number of messages to fetch per request (default: 100)
            
        Returns:
            Generator yielding message dictionaries. Each message contains:
            - id: Message ID
            - provider_id: Provider's message ID
            - sender_id: ID of the message sender
            - text: Message text content
            - attachments: List of attachments (images, videos, audio, files)
            - chat_id: ID of the chat
            - timestamp: Message timestamp
            - is_sender: Whether the current user is the sender
            - reactions: List of reactions to the message
            - quoted: Quoted message details (if this is a reply)
            And more message metadata
            
        Raises:
            requests.exceptions.RequestException: If the API request fails
        """
        cursor = None
        offset = 0
        account_id = self._account(account_id)
        
        while True:
            # Prepare URL and parameters
            url = f"{self.base_url}/v2/{account_id}/chats/{chat_id}/messages"
            params: Dict[str, Any] = {'limit': batch_size, 'offset': offset}
            if cursor:
                params['cursor'] = cursor
                params.pop('offset', None)
                
            # Make API request
            response = requests.get(
                url, headers=self.headers, params=params, timeout=60
            )
            response.raise_for_status()
            
            data = response.json()
            
            messages = data.get("data") or data.get("items") or []
            for message in messages:
                yield message

            cursor = data.get('next_cursor')
            if cursor:
                continue
            if not messages or len(messages) < batch_size:
                break
            offset += batch_size

    def get_messages_as_list(
        self,
        account_id: str,
        chat_id: str,
        batch_size: int = 100,
    ) -> List[Dict]:
        """
        Get all messages from a chat as a list
        
        Args:
            chat_id: The ID of the chat to get messages from
            batch_size: Number of messages to fetch per request (default: 100)
            
        Returns:
            List of message dictionaries
            
        Raises:
            requests.exceptions.RequestException: If the API request fails
        """
        return list(self.get_all_messages(account_id, chat_id, batch_size))

    def get_emails(self, account_id: str, limit: int = 10) -> List[Dict]:
        """
        Get emails for a specific account
        
        Args:
            account_id: The ID of the account to get emails from
            limit: Maximum number of emails to return (default: 10)
            
        Returns:
            List of email dictionaries from the items array. Each email contains:
            - id: Email ID
            - account_id: The associated account ID
            - type: Email type (MAIL)
            - date: Email timestamp
            - role: Email role (inbox, sent, etc.)
            - folders: List of folder names
            - subject: Email subject
            - body: Email body (HTML)
            - body_plain: Email body (plain text)
            - from_attendee: Sender information
            - to_attendees: Recipients information
            - attachments: List of attachments
            And more email metadata
            
        Raises:
            requests.exceptions.RequestException: If the API request fails
        """
        account_id = self._account(account_id)
        url = f"{self.base_url}/v2/{account_id}/emails"
        params = {'limit': limit}
        response = requests.get(
            url, headers=self.headers, params=params, timeout=60
        )
        response.raise_for_status()
        data = response.json()
        
        return data.get("data") or data.get("items") or []

# Example usage:
