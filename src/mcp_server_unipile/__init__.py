import argparse
import asyncio
import logging
import os
from typing import Optional
from . import server

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main() -> None:
    """
    Main entry point for the unipile MCP Server.
    Uses UNIPILE_V2_BASE_URL and UNIPILE_V2_API_KEY for authentication.
    """
    logger.info("Starting mcp-server-unipile")
    
    base_url = os.getenv("UNIPILE_V2_BASE_URL", "https://api.unipile.com")
    api_key = os.getenv("UNIPILE_V2_API_KEY")

    if not api_key:
        logger.error("UNIPILE_V2_API_KEY environment variable is required")
        raise ValueError("UNIPILE_V2_API_KEY environment variable must be set")
    
    logger.info("Starting server with provided credentials")
    asyncio.run(server.main(base_url=base_url, api_key=api_key))
    logger.info("Server shutdown complete")

if __name__ == "__main__":
    main()

# Expose important items at package level
__all__ = ["main", "server"]
