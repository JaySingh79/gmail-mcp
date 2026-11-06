"""SQLite persistence layer for the Gmail Automation MCP Server."""

from gmail_mcp.storage.database import DatabaseManager
from gmail_mcp.storage.models import (
    BulkPlanRecord,
    ScheduledJobRecord,
    SendLedgerRecord,
    SyncStateRecord,
    TemplateRecord,
)
from gmail_mcp.storage.repositories import (
    BulkPlanRepository,
    QuotaWindowRepository,
    ScheduledJobRepository,
    SendLedgerRepository,
    SyncStateRepository,
    TemplateRepository,
)

__all__ = [
    "BulkPlanRecord",
    "BulkPlanRepository",
    "DatabaseManager",
    "QuotaWindowRepository",
    "ScheduledJobRecord",
    "ScheduledJobRepository",
    "SendLedgerRecord",
    "SendLedgerRepository",
    "SyncStateRecord",
    "SyncStateRepository",
    "TemplateRecord",
    "TemplateRepository",
]
