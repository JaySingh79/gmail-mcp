"""Core services for the Gmail Automation MCP Server."""

from gmail_mcp.core.gmail_client import GmailClientWrapper
from gmail_mcp.core.mime_builder import MimeBuilder
from gmail_mcp.core.pagination import build_gmail_search_query, collect_paginated_items
from gmail_mcp.core.quota_manager import QuotaExhaustedError, QuotaManager
from gmail_mcp.core.rate_limiter import RateLimiter, WeightedTokenBucket
from gmail_mcp.core.retry_manager import GmailMCPError, RetryManager
from gmail_mcp.core.safety import SafetyError, SafetyGuard

__all__ = [
    "GmailClientWrapper",
    "GmailMCPError",
    "MimeBuilder",
    "QuotaExhaustedError",
    "QuotaManager",
    "RateLimiter",
    "RetryManager",
    "SafetyError",
    "SafetyGuard",
    "WeightedTokenBucket",
    "build_gmail_search_query",
    "collect_paginated_items",
]
