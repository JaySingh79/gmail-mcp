"""OAuth 2.0 authentication and token management for the Gmail Automation MCP Server."""

from gmail_mcp.auth.credentials import (
    DEFAULT_SCOPES,
    SCOPE_GMAIL_MODIFY,
    SCOPE_GMAIL_SETTINGS_BASIC,
    TOOL_SCOPE_REQUIREMENTS,
    build_insufficient_scope_report,
)
from gmail_mcp.auth.oauth import OAuthFlowManager
from gmail_mcp.auth.token_manager import AuthStatusReport, TokenManager

__all__ = [
    "DEFAULT_SCOPES",
    "SCOPE_GMAIL_MODIFY",
    "SCOPE_GMAIL_SETTINGS_BASIC",
    "TOOL_SCOPE_REQUIREMENTS",
    "AuthStatusReport",
    "OAuthFlowManager",
    "TokenManager",
    "build_insufficient_scope_report",
]
