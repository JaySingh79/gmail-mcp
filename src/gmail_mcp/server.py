"""FastMCP server registration, lifespan dependency injection, and CLI entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from gmail_mcp.config import GmailConfig
from gmail_mcp.core.gmail_client import GmailClientWrapper
from gmail_mcp.tools import (
    ScheduledSendWorker,
    gmail_apply_label,
    gmail_attach_files_to_message,
    gmail_auth_status,
    gmail_bulk_attach_to_drafts,
    gmail_bulk_create_drafts,
    gmail_bulk_label_messages,
    gmail_bulk_send_drafts,
    gmail_cancel_scheduled_send,
    gmail_create_draft,
    gmail_create_filter,
    gmail_create_label,
    gmail_create_template,
    gmail_delete_draft,
    gmail_delete_filter,
    gmail_delete_label,
    gmail_delete_template,
    gmail_duplicate_draft,
    gmail_execute_bulk_reply,
    gmail_execute_bulk_send,
    gmail_export_messages,
    gmail_forward_message,
    gmail_get_attachment,
    gmail_get_bulk_status,
    gmail_get_draft,
    gmail_get_filter,
    gmail_get_mailbox_settings,
    gmail_get_mailbox_stats,
    gmail_get_message,
    gmail_get_profile,
    gmail_get_runtime_limits,
    gmail_get_template,
    gmail_get_thread,
    gmail_health_check,
    gmail_list_attachments,
    gmail_list_drafts,
    gmail_list_filters,
    gmail_list_labels,
    gmail_list_scheduled_sends,
    gmail_list_templates,
    gmail_list_unread,
    gmail_modify_messages,
    gmail_prepare_bulk_reply,
    gmail_prepare_bulk_send,
    gmail_preview_template,
    gmail_process_due_scheduled_sends,
    gmail_remove_label,
    gmail_schedule_send,
    gmail_search_messages,
    gmail_search_threads,
    gmail_send_draft,
    gmail_send_email,
    gmail_send_reply,
    gmail_send_reply_all,
    gmail_start_watch,
    gmail_stop_watch,
    gmail_sync_history,
    gmail_update_draft,
    gmail_update_label,
    gmail_update_template,
    gmail_update_vacation_settings,
)

READ_ONLY_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)

WRITE_SAFE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)

WRITE_IDEMPOTENT_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)

DESTRUCTIVE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=True,
)


def create_gmail_mcp_server(
    client: GmailClientWrapper | None = None,
    *,
    start_scheduler: bool = True,
) -> tuple[FastMCP, GmailClientWrapper]:
    """
    Create and configure the Gmail Automation FastMCP server with shared dependency injection.
    All 60 tools share the same `GmailClientWrapper`, `QuotaManager`, `RateLimiter`, and `DatabaseManager`.
    """
    shared_client = client or GmailClientWrapper(GmailConfig())
    scheduler_worker = ScheduledSendWorker(shared_client)

    @asynccontextmanager
    async def server_lifespan(_app: FastMCP) -> AsyncIterator[dict[str, Any]]:
        if start_scheduler:
            scheduler_worker.start()
        try:
            yield {"client": shared_client, "scheduler": scheduler_worker}
        finally:
            if start_scheduler:
                await scheduler_worker.stop()

    mcp = FastMCP(
        name="gmail-automation-mcp",
        instructions=(
            "Production-grade Gmail Automation MCP Server using official Gmail REST API v1. "
            "Enforces per-user/project quota tracking, low-latency metadata search, SQLite templates, "
            "MIME-preserving draft duplication/bulk attachment, and two-phase confirmed bulk sending."
        ),
        lifespan=server_lifespan,
    )

    # -------------------------------------------------------------------------
    # A. Account and Diagnostics (5 Tools)
    # -------------------------------------------------------------------------

    @mcp.tool(
        name="gmail_get_profile",
        description=(
            "Retrieve authenticated user profile information including primary email address, "
            "total messages count (`messagesTotal`), total conversations count (`threadsTotal`), "
            "and the latest mailbox sync checkpoint (`historyId`). "
            "Use this tool as a first reconnaissance step to identify which user mailbox is currently connected."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_get_profile() -> dict[str, Any]:
        return await gmail_get_profile(shared_client)

    @mcp.tool(
        name="gmail_auth_status",
        description=(
            "Inspect OAuth 2.0 authentication state, token presence, expiration UTC timestamp, "
            "refresh token availability, and granted Google API scopes (e.g. gmail.modify, gmail.settings.basic). "
            "When `verify_connectivity=True`, performs a live probe against Google endpoints to confirm end-to-end API readiness. "
            "Use this tool to diagnose authentication failures, permission errors, or before starting complex automation workflows."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_auth_status(
        verify_connectivity: bool = Field(
            default=True,
            description="When True, executes a live API probe against Google endpoints to verify network connectivity and token validity.",
        ),
    ) -> dict[str, Any]:
        return await gmail_auth_status(shared_client, verify_connectivity=verify_connectivity)

    @mcp.tool(
        name="gmail_get_runtime_limits",
        description=(
            "Retrieve Gmail API runtime limits, 240-second quota metrics, estimated Google API quota units consumed, "
            "and 24-hour daily email sending headroom based on account type (free @gmail.com vs Google Workspace). "
            "Use this tool before running large bulk email operations or intensive search queries to avoid hitting rate limits."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_get_runtime_limits(
        reconcile_sent_mail: bool = Field(
            default=False,
            description="When True, queries recent sent emails from the mailbox to synchronize and calibrate local quota estimation with actual external sending activity.",
        ),
    ) -> dict[str, Any]:
        return await gmail_get_runtime_limits(shared_client, reconcile_sent_mail=reconcile_sent_mail)

    @mcp.tool(
        name="gmail_health_check",
        description=(
            "Run a comprehensive health diagnostic across all server subsystems: verifies Gmail API connectivity, "
            "OAuth token validity, local SQLite database migration status, and allowed filesystem directory access permissions. "
            "Use this tool to verify system readiness after server startup or configuration changes."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_health_check() -> dict[str, Any]:
        return await gmail_health_check(shared_client)

    @mcp.tool(
        name="gmail_get_mailbox_stats",
        description=(
            "Retrieve detailed aggregated mailbox statistics, total message and thread counts, unread counters, "
            "and breakdown across core system folders (INBOX, SENT, TRASH, SPAM, DRAFT) and optional user labels. "
            "Use this tool to provide mailbox summaries or monitor folder volume without downloading individual messages."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_get_mailbox_stats(
        include_user_labels: bool = Field(
            default=True,
            description="When True, includes statistics and message/thread counts for custom user-created labels in addition to core system folders.",
        ),
    ) -> dict[str, Any]:
        return await gmail_get_mailbox_stats(shared_client, include_user_labels=include_user_labels)

    # -------------------------------------------------------------------------
    # B. Search and Email Retrieval (7 Tools)
    # -------------------------------------------------------------------------

    @mcp.tool(
        name="gmail_search_messages",
        description=(
            "Search emails server-side using standard Gmail query syntax (`is:unread`, `from:recruiter@company.com`, `has:attachment`, etc.). "
            "Returns structured message headers and summaries (ID, snippet, subject, sender, date, label IDs) with bounded concurrency. "
            "Defaults to `format='metadata'` for low latency and never marks unread emails as read. "
            "To view the full plaintext/HTML body of an email, retrieve its message ID with this tool and then pass it to `gmail_get_message`."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_search_messages(
        query: str | None = Field(
            default=None,
            description="Free-form Gmail search query supporting all Gmail operators (e.g., 'is:unread from:boss@example.com subject:quarterly').",
        ),
        max_results: int = Field(
            default=20,
            ge=1,
            le=500,
            description="Maximum number of messages to return in this query (between 1 and 500, default 20).",
        ),
        page_token: str | None = Field(
            default=None,
            description="Pagination token returned from a previous search call to retrieve the next page of results.",
        ),
        format: Literal["minimal", "metadata", "full", "raw"] = Field(
            default="metadata",
            description="Message detail format: 'minimal' (IDs & label IDs only), 'metadata' (headers like Subject/From/Date + snippet), 'full' (complete decoded body), or 'raw'.",
        ),
        fetch_details: bool = Field(
            default=True,
            description="When True, concurrently fetches header details for all matched message IDs; when False, returns only IDs and thread IDs.",
        ),
        auto_paginate: bool = Field(
            default=False,
            description="When True, automatically traverses all pagination tokens until max_results is reached or all matching messages are fetched.",
        ),
        include_spam_trash: bool = Field(
            default=False,
            description="When True, includes messages from SPAM and TRASH folders in search results.",
        ),
        sender: str | None = Field(
            default=None,
            description="Convenience filter for sender email address or domain (automatically converted into 'from:<sender>').",
        ),
        recipient: str | None = Field(
            default=None,
            description="Convenience filter for recipient email address (automatically converted into 'to:<recipient>').",
        ),
        subject: str | None = Field(
            default=None,
            description="Convenience filter for subject line text (automatically converted into 'subject:<subject>').",
        ),
        label: str | None = Field(
            default=None,
            description="Convenience filter for a specific label name or ID (automatically converted into 'label:<label>').",
        ),
        after: str | None = Field(
            default=None,
            description="Filter messages received after date formatted as 'YYYY/MM/DD' or epoch seconds.",
        ),
        before: str | None = Field(
            default=None,
            description="Filter messages received before date formatted as 'YYYY/MM/DD' or epoch seconds.",
        ),
        newer_than: str | None = Field(
            default=None,
            description="Filter messages newer than duration, e.g. '2d' (2 days), '1m' (1 month), '5h' (5 hours).",
        ),
        older_than: str | None = Field(
            default=None,
            description="Filter messages older than duration, e.g. '7d' (7 days), '1y' (1 year).",
        ),
        has_attachment: bool | None = Field(
            default=None,
            description="When True, filters strictly to messages with file attachments; when False, filters to messages without attachments.",
        ),
        filename: str | None = Field(
            default=None,
            description="Filter messages with attachments matching a specific filename or file extension (e.g. 'resume.pdf' or 'pdf').",
        ),
        is_unread: bool | None = Field(
            default=None,
            description="When True, filters strictly to unread messages (`is:unread`); when False, filters to read messages (`is:read`).",
        ),
        rfc822msgid: str | None = Field(
            default=None,
            description="Exact RFC 2822 Message-ID header value (e.g. '<123456789@mail.gmail.com>') for precise single-email lookup.",
        ),
    ) -> dict[str, Any]:
        return await gmail_search_messages(
            shared_client,
            query=query,
            max_results=max_results,
            page_token=page_token,
            format=format,
            fetch_details=fetch_details,
            auto_paginate=auto_paginate,
            include_spam_trash=include_spam_trash,
            sender=sender,
            recipient=recipient,
            subject=subject,
            label=label,
            after=after,
            before=before,
            newer_than=newer_than,
            older_than=older_than,
            has_attachment=has_attachment,
            filename=filename,
            is_unread=is_unread,
            rfc822msgid=rfc822msgid,
        )

    @mcp.tool(
        name="gmail_search_threads",
        description=(
            "Search email conversations grouped into threads using standard Gmail search syntax. "
            "Returns conversation thread IDs, snippets, history IDs, and message counts. "
            "Use this tool when you need multi-turn conversation context rather than isolated single messages."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_search_threads(
        query: str = Field(
            default="",
            description="Gmail search query string to filter conversation threads (e.g. 'is:unread', 'from:client@domain.com').",
        ),
        max_results: int = Field(
            default=20,
            ge=1,
            le=500,
            description="Maximum number of conversation threads to return (default 20, max 500).",
        ),
        page_token: str | None = Field(
            default=None,
            description="Pagination token to retrieve the next page of conversation threads.",
        ),
        auto_paginate: bool = Field(
            default=False,
            description="When True, automatically paginates through search results until max_results is reached.",
        ),
        include_spam_trash: bool = Field(
            default=False,
            description="When True, includes conversation threads located in SPAM or TRASH folders.",
        ),
    ) -> dict[str, Any]:
        return await gmail_search_threads(
            shared_client,
            query=query,
            max_results=max_results,
            page_token=page_token,
            auto_paginate=auto_paginate,
            include_spam_trash=include_spam_trash,
        )

    @mcp.tool(
        name="gmail_get_message",
        description=(
            "Retrieve complete details of a specific email by its Gmail internal `message_id` (e.g. '18db392a10b4f3e1') "
            "or RFC 2822 Message-ID header. "
            "Returns parsed headers (From, To, Cc, Subject, Date), fully decoded plain text body, HTML body, "
            "and attachment metadata. Does NOT mark the email as read."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_get_message(
        message_id: str | None = Field(
            default=None,
            description="Internal hexadecimal Gmail message ID (e.g. '192d1847afb3c201'). Preferred identifier.",
        ),
        rfc822msgid: str | None = Field(
            default=None,
            description="Standard Internet Message-ID header (e.g. '<caf123@mail.gmail.com>') used when internal message_id is not known.",
        ),
        format: Literal["minimal", "metadata", "full", "raw"] = Field(
            default="full",
            description="Response format: 'full' (parsed headers + decoded text & HTML bodies), 'metadata' (headers only), 'minimal' (IDs only), or 'raw'.",
        ),
    ) -> dict[str, Any]:
        return await gmail_get_message(
            shared_client,
            message_id=message_id,
            rfc822msgid=rfc822msgid,
            format=format,
        )

    @mcp.tool(
        name="gmail_get_thread",
        description=(
            "Retrieve an entire email conversation thread by its `thread_id`. "
            "Returns all messages in the thread in chronological order, including headers, participants, bodies, "
            "and attachments for each turn in the discussion. "
            "Use this tool to read the complete backstory before drafting a contextual reply."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_get_thread(
        thread_id: str = Field(
            ...,
            description="Gmail thread ID (e.g. '18db392a10b4f3e1') identifying the email conversation.",
        ),
        format: Literal["minimal", "metadata", "full"] = Field(
            default="full",
            description="Detail level for each message in the thread: 'full' (complete decoded bodies), 'metadata' (headers only), or 'minimal'.",
        ),
    ) -> dict[str, Any]:
        return await gmail_get_thread(shared_client, thread_id=thread_id, format=format)

    @mcp.tool(
        name="gmail_list_unread",
        description=(
            "Quickly retrieve unread emails from the inbox with automatic pagination support. "
            "Returns message headers, snippets, and unread counts without losing results across pages. "
            "Safety guarantee: Never modifies message labels or marks unread emails as read unless `mark_as_read=True` is explicitly passed. "
            "Ideal for user requests like 'show my unread emails' or 'check for new messages'."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_list_unread(
        query: str | None = Field(
            default=None,
            description="Optional additional search query to narrow down unread emails (e.g. 'from:boss@company.com' or 'has:attachment').",
        ),
        max_results_per_page: int = Field(
            default=500,
            ge=1,
            le=500,
            description="Number of unread messages requested per API batch request (default 500).",
        ),
        max_total_results: int | None = Field(
            default=None,
            description="Total ceiling of unread messages to retrieve across all traversed pages (default retrieves all matching unread emails).",
        ),
        page_token: str | None = Field(
            default=None,
            description="Pagination token to resume retrieving unread messages from a previous call.",
        ),
        include_body: bool = Field(
            default=False,
            description="When True, fetches and parses the full plaintext body for each unread email; when False, retrieves fast metadata only.",
        ),
        mark_as_read: bool = Field(
            default=False,
            description="Safety flag: Only when explicitly True will retrieved messages have their UNREAD label removed. Default is False (safe read-only).",
        ),
    ) -> dict[str, Any]:
        return await gmail_list_unread(
            shared_client,
            query=query,
            max_results_per_page=max_results_per_page,
            max_total_results=max_total_results,
            page_token=page_token,
            include_body=include_body,
            mark_as_read=mark_as_read,
        )

    @mcp.tool(
        name="gmail_get_attachment",
        description=(
            "Download an email attachment by its `attachment_id` and `message_id`, saving it securely to disk "
            "within the project's allowed downloads directory. "
            "Returns the destination file path, original filename, MIME type, and byte size. "
            "Use `gmail_list_attachments` or `gmail_get_message` first to obtain attachment IDs."
        ),
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_get_attachment(
        message_id: str = Field(
            ...,
            description="Gmail message ID containing the attachment.",
        ),
        attachment_id: str = Field(
            ...,
            description="Gmail attachment ID extracted from message metadata.",
        ),
        destination_directory: str = Field(
            default="downloads",
            description="Local subfolder or absolute directory path to save the attachment. Must resolve inside allowed directories.",
        ),
        filename: str | None = Field(
            default=None,
            description="Custom filename for the saved file. If omitted, uses the attachment's original sanitized filename.",
        ),
    ) -> dict[str, Any]:
        return await gmail_get_attachment(
            shared_client,
            message_id=message_id,
            attachment_id=attachment_id,
            destination_directory=destination_directory,
            filename=filename,
        )

    @mcp.tool(
        name="gmail_export_messages",
        description=(
            "Bulk export emails matching a Gmail search query to local disk. "
            "Supports export formats: 'json' (structured summaries), 'csv' (tabular spreadsheet of metadata), "
            "or 'eml' (standard RFC 822 MIME files suitable for backup or email clients). "
            "Outputs are saved within the project's allowed exports directory."
        ),
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_export_messages(
        query: str = Field(
            ...,
            description="Gmail search query defining which messages to export (e.g. 'label:receipts after:2026/01/01').",
        ),
        export_format: Literal["json", "csv", "eml"] = Field(
            default="json",
            description="Export file format: 'json' (structured metadata), 'csv' (spreadsheet), or 'eml' (raw RFC 822 email files).",
        ),
        destination_directory: str = Field(
            default="exports",
            description="Subfolder or path where exported files will be written. Must reside within allowed directories.",
        ),
        filename_prefix: str = Field(
            default="gmail_export",
            description="Filename prefix for generated export files.",
        ),
        max_results: int = Field(
            default=50,
            ge=1,
            le=1000,
            description="Maximum number of matching messages to export (default 50, max 1000).",
        ),
        include_attachments: bool = Field(
            default=False,
            description="When True, downloads and exports file attachments associated with each exported message.",
        ),
    ) -> dict[str, Any]:
        return await gmail_export_messages(
            shared_client,
            query=query,
            export_format=export_format,
            destination_directory=destination_directory,
            filename_prefix=filename_prefix,
            max_results=max_results,
            include_attachments=include_attachments,
        )

    # -------------------------------------------------------------------------
    # C. Draft Creation and Management (8 Tools)
    # -------------------------------------------------------------------------

    @mcp.tool(
        name="gmail_create_draft",
        description=(
            "Create a new Gmail draft without sending it. "
            "Use this tool whenever drafting an email, staging a job application pitch, or preparing an email for human review. "
            "Supports multiple recipients (To, CC, BCC), subject, plain text body, rich HTML body, local file attachments, "
            "and template variable substitution (using `template_id` and `variables`). "
            "Returns the created draft ID and message ID."
        ),
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_create_draft(
        to: str | list[str] = Field(
            ...,
            description="Primary recipient email address or list of recipient email addresses (e.g. 'hiring@company.com' or ['a@b.com', 'c@d.com']).",
        ),
        subject: str | None = Field(
            default=None,
            description="Email subject line. Required unless specified by a referenced template.",
        ),
        body_text: str | None = Field(
            default=None,
            description="Plain text content of the email body.",
        ),
        body_html: str | None = Field(
            default=None,
            description="Optional HTML formatted email body. Plain text version is automatically synthesized if body_text is omitted.",
        ),
        cc: str | list[str] | None = Field(
            default=None,
            description="Optional Carbon Copy (CC) recipient email address or list of addresses.",
        ),
        bcc: str | list[str] | None = Field(
            default=None,
            description="Optional Blind Carbon Copy (BCC) recipient email address or list of addresses.",
        ),
        reply_to: str | None = Field(
            default=None,
            description="Optional Reply-To header email address.",
        ),
        thread_id: str | None = Field(
            default=None,
            description="Optional Gmail thread ID to associate this draft with an existing conversation thread.",
        ),
        in_reply_to: str | None = Field(
            default=None,
            description="Optional Message-ID header of the parent message when drafting a threaded reply.",
        ),
        references: str | None = Field(
            default=None,
            description="Optional References header string for thread grouping.",
        ),
        template_id: str | None = Field(
            default=None,
            description="Optional ID of a saved SQLite template to render subject and body content.",
        ),
        variables: dict[str, Any] | None = Field(
            default=None,
            description="Dictionary of key-value pairs (e.g. {'role': 'AI Engineer', 'company': 'Acme Corp'}) to substitute into template placeholders.",
        ),
        attachments: list[str] | None = Field(
            default=None,
            description="List of local file paths to attach to the draft (must reside within allowed project directories).",
        ),
    ) -> dict[str, Any]:
        return await gmail_create_draft(
            shared_client,
            to=to,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            cc=cc,
            bcc=bcc,
            reply_to=reply_to,
            thread_id=thread_id,
            in_reply_to=in_reply_to,
            references=references,
            template_id=template_id,
            variables=variables,
            attachments=attachments,
        )

    @mcp.tool(
        name="gmail_get_draft",
        description=(
            "Retrieve details of an existing draft by its `draft_id`. "
            "Returns parsed recipient headers, subject line, plain text body, HTML body, and attachment information. "
            "Use this tool to review or verify an existing draft before sending or modifying it."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_get_draft(
        draft_id: str = Field(
            ...,
            description="Gmail draft ID (e.g. 'r-1234567890123456789') to retrieve.",
        ),
        format: Literal["minimal", "metadata", "full", "raw"] = Field(
            default="full",
            description="Detail level: 'full' (parsed headers and decoded body), 'metadata' (headers only), 'minimal' (ID only), or 'raw'.",
        ),
    ) -> dict[str, Any]:
        return await gmail_get_draft(shared_client, draft_id=draft_id, format=format)

    @mcp.tool(
        name="gmail_list_drafts",
        description=(
            "List existing drafts in the mailbox with pagination support. "
            "Returns draft IDs, associated message IDs, subjects, snippets, recipient summaries, and last updated timestamps. "
            "Use this tool to find pending drafts or browse un-sent drafts."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_list_drafts(
        query: str | None = Field(
            default=None,
            description="Optional search query filter to locate specific drafts matching keywords.",
        ),
        max_results: int = Field(
            default=50,
            ge=1,
            le=500,
            description="Maximum number of drafts to return in this request (default 50, max 500).",
        ),
        page_token: str | None = Field(
            default=None,
            description="Pagination token to fetch the next page of drafts.",
        ),
        include_spam_trash: bool = Field(
            default=False,
            description="When True, includes drafts residing in Spam or Trash folders.",
        ),
        auto_paginate: bool = Field(
            default=False,
            description="When True, automatically paginates through results up to max_results.",
        ),
    ) -> dict[str, Any]:
        return await gmail_list_drafts(
            shared_client,
            query=query,
            max_results=max_results,
            page_token=page_token,
            include_spam_trash=include_spam_trash,
            auto_paginate=auto_paginate,
        )

    @mcp.tool(
        name="gmail_duplicate_draft",
        description=(
            "Duplicate an existing draft 1 to N times, perfectly cloning its MIME structure, HTML styling, "
            "and all attached files. Supports overriding recipient addresses and subject line per copy. "
            "Use this tool when creating tailored outreach emails from a master draft template without re-uploading attachments."
        ),
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_duplicate_draft(
        source_draft_id: str = Field(
            ...,
            description="ID of the existing master draft to clone.",
        ),
        copies: int = Field(
            default=1,
            ge=1,
            le=100,
            description="Number of duplicate drafts to create (between 1 and 100, default 1).",
        ),
        recipient_overrides: list[str] | None = Field(
            default=None,
            description="Optional list of recipient email addresses, assigned sequentially to each cloned copy.",
        ),
        subject_override: str | None = Field(
            default=None,
            description="Optional custom subject line to apply across all cloned drafts.",
        ),
        preserve_thread: bool = Field(
            default=False,
            description="When True, preserves the original conversation thread association in the duplicated drafts.",
        ),
    ) -> dict[str, Any]:
        return await gmail_duplicate_draft(
            shared_client,
            source_draft_id=source_draft_id,
            copies=copies,
            recipient_overrides=recipient_overrides,
            subject_override=subject_override,
            preserve_thread=preserve_thread,
        )

    @mcp.tool(
        name="gmail_update_draft",
        description=(
            "Modify an existing draft's recipients, subject, plain text or HTML body, and attachments. "
            "By default, preserves existing file attachments on the draft (`preserve_existing_attachments=True`) "
            "and appends any newly specified attachments."
        ),
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_update_draft(
        draft_id: str = Field(
            ...,
            description="ID of the draft to update.",
        ),
        to: str | list[str] | None = Field(
            default=None,
            description="Updated primary recipient(s). If omitted, keeps existing recipients.",
        ),
        cc: str | list[str] | None = Field(
            default=None,
            description="Updated CC recipient(s).",
        ),
        bcc: str | list[str] | None = Field(
            default=None,
            description="Updated BCC recipient(s).",
        ),
        subject: str | None = Field(
            default=None,
            description="Updated email subject line. If omitted, keeps existing subject.",
        ),
        body_text: str | None = Field(
            default=None,
            description="Updated plain text body. If omitted, keeps existing text body.",
        ),
        body_html: str | None = Field(
            default=None,
            description="Updated HTML formatted body.",
        ),
        additional_attachments: list[str] | None = Field(
            default=None,
            description="List of local file paths to append to the draft.",
        ),
        preserve_existing_attachments: bool = Field(
            default=True,
            description="When True, keeps all existing attachments on the draft and adds new ones; when False, replaces them.",
        ),
    ) -> dict[str, Any]:
        return await gmail_update_draft(
            shared_client,
            draft_id=draft_id,
            to=to,
            cc=cc,
            bcc=bcc,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            additional_attachments=additional_attachments,
            preserve_existing_attachments=preserve_existing_attachments,
        )

    @mcp.tool(
        name="gmail_delete_draft",
        description=(
            "Permanently delete an email draft by its `draft_id`. "
            "Safety note: This action is destructive and irreversible. Use only when draft removal is explicitly requested."
        ),
        annotations=DESTRUCTIVE_ANNOTATIONS,
    )
    async def _tool_gmail_delete_draft(
        draft_id: str = Field(
            ...,
            description="ID of the draft to permanently delete.",
        ),
    ) -> dict[str, Any]:
        return await gmail_delete_draft(shared_client, draft_id=draft_id)

    @mcp.tool(
        name="gmail_bulk_create_drafts",
        description=(
            "Generate 1 to 100+ personalized email drafts in batch from a recipient list and template, without sending them. "
            "Interpolates variables like `{first_name}`, `{company}` per recipient and creates staged drafts in Gmail. "
            "Use this tool for personalized outreach campaigns so the user can inspect drafts in Gmail before dispatching."
        ),
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_bulk_create_drafts(
        recipients: list[dict[str, Any]] | None = Field(
            default=None,
            description="List of recipient dictionaries, each containing 'to' (email string) and optional template variables (e.g. [{'to': 'a@b.com', 'name': 'Alice'}]).",
        ),
        recipient_source: str | None = Field(
            default=None,
            description="Optional CSV or JSON file path containing recipient or lead records.",
        ),
        field_mapping: dict[str, str] | None = Field(
            default=None,
            description="Optional dictionary mapping target template variables to source keys (e.g. {'email': 'Contact Email', 'company': 'Company Name'}).",
        ),
        attachment_pattern: str | None = Field(
            default=None,
            description="Optional attachment path template string (e.g. 'D:/Resumes/{company}_cover_letter.pdf') to attach per-recipient files.",
        ),
        template_id: str | None = Field(
            default=None,
            description="ID of a saved SQLite template to use for subject and body generation.",
        ),
        subject: str | None = Field(
            default=None,
            description="Subject template string (supports {placeholders}). Used if template_id is omitted.",
        ),
        body_text: str | None = Field(
            default=None,
            description="Plain text body template (supports {placeholders}).",
        ),
        body_html: str | None = Field(
            default=None,
            description="Optional HTML body template (supports {placeholders}).",
        ),
        attachments: list[str] | None = Field(
            default=None,
            description="Common list of local file paths to attach to every created draft.",
        ),
        compact: bool = Field(
            default=True,
            description="When True, returns a lightweight summary with created draft IDs and sample items, preventing context window bloating.",
        ),
    ) -> dict[str, Any]:
        return await gmail_bulk_create_drafts(
            shared_client,
            recipients=recipients,
            recipient_source=recipient_source,
            field_mapping=field_mapping,
            attachment_pattern=attachment_pattern,
            template_id=template_id,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            attachments=attachments,
            compact=compact,
        )

    @mcp.tool(
        name="gmail_bulk_attach_to_drafts",
        description=(
            "Attach one or multiple documents to multiple existing drafts in batch. "
            "Supports adding a shared set of files to all specified drafts, or mapping unique files to individual draft IDs. "
            "Preserves MIME structure and existing draft content."
        ),
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_bulk_attach_to_drafts(
        draft_ids: list[str] = Field(
            ...,
            description="List of target draft IDs to attach documents onto.",
        ),
        attachments: list[str] | None = Field(
            default=None,
            description="Common file paths to attach to all specified drafts.",
        ),
        per_draft_attachments: dict[str, list[str]] | None = Field(
            default=None,
            description="Optional mapping of specific draft_id to a list of draft-specific file paths.",
        ),
    ) -> dict[str, Any]:
        return await gmail_bulk_attach_to_drafts(
            shared_client,
            draft_ids=draft_ids,
            attachments=attachments,
            per_draft_attachments=per_draft_attachments,
        )

    # -------------------------------------------------------------------------
    # D. Sending, Replying, and Forwarding (5 Tools)
    # -------------------------------------------------------------------------

    @mcp.tool(
        name="gmail_send_draft",
        description=(
            "Dispatch an existing draft directly via Gmail API's native `users.drafts.send`. "
            "Sends the staged draft immediately without re-rendering bodies or re-uploading attachments. "
            "Records the sent email into the local SQLite ledger and cleans up the draft automatically."
        ),
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_send_draft(
        draft_id: str = Field(
            ...,
            description="The unique Gmail draft ID to send (e.g. 'r-1234567890').",
        ),
        idempotency_key: str | None = Field(
            default=None,
            description="Optional unique token to prevent duplicate dispatch if re-executed.",
        ),
    ) -> dict[str, Any]:
        return await gmail_send_draft(
            shared_client,
            draft_id=draft_id,
            idempotency_key=idempotency_key,
        )

    @mcp.tool(
        name="gmail_send_email",
        description=(
            "Send a single email immediately via the Gmail API. "
            "Safety confirmation gate: Requires `confirm_send=True` to actually transmit the email. "
            "If `confirm_send=False` (default) or `dry_run=True`, returns a full pre-flight validation preview "
            "showing recipients, rendered subject, body preview, attachment sizes, and quota impact without sending. "
            "Includes duplicate send protection and idempotency key support."
        ),
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_send_email(
        to: str | list[str] = Field(
            ...,
            description="Recipient email address or list of recipient email addresses.",
        ),
        subject: str | None = Field(
            default=None,
            description="Email subject line. Required unless specified via template_id.",
        ),
        body_text: str | None = Field(
            default=None,
            description="Plain text email body content.",
        ),
        body_html: str | None = Field(
            default=None,
            description="Optional HTML formatted email body.",
        ),
        cc: str | list[str] | None = Field(
            default=None,
            description="Optional CC recipient(s).",
        ),
        bcc: str | list[str] | None = Field(
            default=None,
            description="Optional BCC recipient(s).",
        ),
        reply_to: str | None = Field(
            default=None,
            description="Optional custom Reply-To header address.",
        ),
        from_alias: str | None = Field(
            default=None,
            description="Optional verified send-as email alias to send from.",
        ),
        custom_headers: dict[str, str] | None = Field(
            default=None,
            description="Optional custom MIME headers dictionary.",
        ),
        template_id: str | None = Field(
            default=None,
            description="Optional ID of a saved SQLite template to render subject and body.",
        ),
        variables: dict[str, Any] | None = Field(
            default=None,
            description="Template variables dictionary for placeholder substitution.",
        ),
        attachments: list[str] | None = Field(
            default=None,
            description="List of local file paths to attach to the email.",
        ),
        idempotency_key: str | None = Field(
            default=None,
            description="Optional unique client idempotency token preventing duplicate sending on network retries.",
        ),
        confirm_send: bool = Field(
            default=False,
            description="Explicit confirmation gate: Must be True to send the email. If False, returns a safety preview.",
        ),
        dry_run: bool = Field(
            default=False,
            description="When True, validates inputs and renders email without transmitting.",
        ),
    ) -> dict[str, Any]:
        return await gmail_send_email(
            shared_client,
            to=to,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            cc=cc,
            bcc=bcc,
            reply_to=reply_to,
            from_alias=from_alias,
            custom_headers=custom_headers,
            template_id=template_id,
            variables=variables,
            attachments=attachments,
            idempotency_key=idempotency_key,
            confirm_send=confirm_send,
            dry_run=dry_run,
        )

    @mcp.tool(
        name="gmail_send_reply",
        description=(
            "Reply to a specific email within an existing conversation thread. "
            "Automatically extracts parent headers, preserves conversation thread ID, and sets RFC `In-Reply-To` and `References`. "
            "Safety confirmation gate: Requires `confirm_send=True` to execute; otherwise returns a preview without sending."
        ),
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_send_reply(
        thread_id: str | None = Field(
            default=None,
            description="Gmail thread ID of the conversation being replied to.",
        ),
        message_id: str | None = Field(
            default=None,
            description="Specific Gmail message ID being replied to (used to extract Subject and In-Reply-To).",
        ),
        body_text: str | None = Field(
            default=None,
            description="Plain text content of the reply.",
        ),
        body_html: str | None = Field(
            default=None,
            description="Optional HTML formatted reply content.",
        ),
        template_id: str | None = Field(
            default=None,
            description="Optional saved template ID for rendering reply text.",
        ),
        variables: dict[str, Any] | None = Field(
            default=None,
            description="Template variables dictionary for placeholder substitution.",
        ),
        attachments: list[str] | None = Field(
            default=None,
            description="List of file paths to attach to the reply.",
        ),
        cc: str | list[str] | None = Field(
            default=None,
            description="Optional CC recipient(s) for the reply.",
        ),
        bcc: str | list[str] | None = Field(
            default=None,
            description="Optional BCC recipient(s) for the reply.",
        ),
        idempotency_key: str | None = Field(
            default=None,
            description="Optional unique client idempotency token preventing duplicate replies.",
        ),
        confirm_send: bool = Field(
            default=False,
            description="Explicit confirmation gate: Must be True to send the reply. If False, returns a preview.",
        ),
        dry_run: bool = Field(
            default=False,
            description="When True, validates inputs and renders the reply without sending.",
        ),
    ) -> dict[str, Any]:
        return await gmail_send_reply(
            shared_client,
            thread_id=thread_id,
            message_id=message_id,
            body_text=body_text,
            body_html=body_html,
            template_id=template_id,
            variables=variables,
            attachments=attachments,
            cc=cc,
            bcc=bcc,
            idempotency_key=idempotency_key,
            confirm_send=confirm_send,
            dry_run=dry_run,
        )

    @mcp.tool(
        name="gmail_send_reply_all",
        description=(
            "Reply to all participants on an existing email thread. "
            "Automatically addresses the original sender, includes all original To and CC recipients, "
            "and excludes the authenticated user's own email address to prevent self-looping. "
            "Safety confirmation gate: Requires `confirm_send=True` to execute; otherwise returns a preview."
        ),
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_send_reply_all(
        thread_id: str | None = Field(
            default=None,
            description="Gmail thread ID of the conversation.",
        ),
        message_id: str | None = Field(
            default=None,
            description="Specific Gmail message ID being replied to.",
        ),
        body_text: str | None = Field(
            default=None,
            description="Plain text content of the reply-all message.",
        ),
        body_html: str | None = Field(
            default=None,
            description="Optional HTML formatted reply-all content.",
        ),
        template_id: str | None = Field(
            default=None,
            description="Optional saved template ID.",
        ),
        variables: dict[str, Any] | None = Field(
            default=None,
            description="Template variables dictionary.",
        ),
        attachments: list[str] | None = Field(
            default=None,
            description="List of file paths to attach to the reply.",
        ),
        bcc: str | list[str] | None = Field(
            default=None,
            description="Optional BCC recipient(s).",
        ),
        idempotency_key: str | None = Field(
            default=None,
            description="Optional unique client idempotency token preventing duplicate replies.",
        ),
        confirm_send: bool = Field(
            default=False,
            description="Explicit confirmation gate: Must be True to send the reply. If False, returns a preview.",
        ),
        dry_run: bool = Field(
            default=False,
            description="When True, validates inputs and renders the reply without sending.",
        ),
    ) -> dict[str, Any]:
        return await gmail_send_reply_all(
            shared_client,
            thread_id=thread_id,
            message_id=message_id,
            body_text=body_text,
            body_html=body_html,
            template_id=template_id,
            variables=variables,
            attachments=attachments,
            bcc=bcc,
            idempotency_key=idempotency_key,
            confirm_send=confirm_send,
            dry_run=dry_run,
        )

    @mcp.tool(
        name="gmail_forward_message",
        description=(
            "Forward an existing email to new recipients, preserving the original message body, original headers, "
            "and all original file attachments, with optional accompanying commentary. "
            "Safety confirmation gate: Requires `confirm_send=True` to execute; otherwise returns a preview."
        ),
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_forward_message(
        message_id: str = Field(
            ...,
            description="Gmail message ID of the email to forward.",
        ),
        to: str | list[str] = Field(
            ...,
            description="Recipient email address or list of addresses to forward to.",
        ),
        comment_text: str = Field(
            default="",
            description="Optional personal note or commentary in plain text placed above the forwarded message.",
        ),
        comment_html: str | None = Field(
            default=None,
            description="Optional HTML formatted commentary placed above the forwarded message.",
        ),
        cc: str | list[str] | None = Field(
            default=None,
            description="Optional CC recipient(s).",
        ),
        bcc: str | list[str] | None = Field(
            default=None,
            description="Optional BCC recipient(s).",
        ),
        include_original_attachments: bool = Field(
            default=True,
            description="When True, downloads and re-attaches original file attachments to the forwarded email.",
        ),
        additional_attachments: list[str] | None = Field(
            default=None,
            description="List of additional local file paths to attach alongside original attachments.",
        ),
        idempotency_key: str | None = Field(
            default=None,
            description="Optional unique client idempotency token.",
        ),
        confirm_send: bool = Field(
            default=False,
            description="Explicit confirmation gate: Must be True to forward the email. If False, returns a preview.",
        ),
        dry_run: bool = Field(
            default=False,
            description="When True, validates inputs and renders forwarded message preview without transmitting.",
        ),
    ) -> dict[str, Any]:
        return await gmail_forward_message(
            shared_client,
            message_id=message_id,
            to=to,
            comment_text=comment_text,
            comment_html=comment_html,
            cc=cc,
            bcc=bcc,
            include_original_attachments=include_original_attachments,
            additional_attachments=additional_attachments,
            idempotency_key=idempotency_key,
            confirm_send=confirm_send,
            dry_run=dry_run,
        )

    # -------------------------------------------------------------------------
    # E. Bulk Sending Workflows (Two-Phase) (6 Tools)
    # -------------------------------------------------------------------------

    @mcp.tool(
        name="gmail_prepare_bulk_send",
        description=(
            "Phase 1 of two-phase bulk sending: Validate recipient lists, render templates, estimate quota consumption, "
            "and create a staged sending plan in SQLite. "
            "Supports arbitrary JSON/CSV lead sources with `field_mapping` and per-recipient `attachment_pattern`. "
            "Returns a unique `plan_id` and itemized preview. Does NOT send any emails. "
            "To execute the staged plan, call `gmail_execute_bulk_send` with the returned `plan_id`."
        ),
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_prepare_bulk_send(
        recipients: list[dict[str, Any]] | None = Field(
            default=None,
            description="List of recipient dictionaries with 'to' and custom template variable key-values.",
        ),
        recipient_source: str | None = Field(
            default=None,
            description="Optional CSV or JSON file path containing recipient rows (columns mapped to template variables).",
        ),
        field_mapping: dict[str, str] | None = Field(
            default=None,
            description="Optional mapping from target fields (to, email, name, company) to source column/key names.",
        ),
        attachment_pattern: str | None = Field(
            default=None,
            description="Optional attachment path template string (e.g. 'D:/Resumes/{company}_cover_letter.pdf') for per-recipient attachments.",
        ),
        template_id: str | None = Field(
            default=None,
            description="ID of a saved SQLite template to use for rendering.",
        ),
        subject: str | None = Field(
            default=None,
            description="Subject template string (if template_id is omitted).",
        ),
        body_text: str | None = Field(
            default=None,
            description="Plain text body template.",
        ),
        body_html: str | None = Field(
            default=None,
            description="Optional HTML body template.",
        ),
        attachments: list[str] | None = Field(
            default=None,
            description="Common attachment file paths to include with every email.",
        ),
        personalize: bool = Field(
            default=True,
            description="When True, interpolates recipient variables into template placeholders.",
        ),
        dry_run: bool = Field(
            default=True,
            description="When True, stages in memory without persisting to SQLite.",
        ),
        reconcile_sent_mail: bool = Field(
            default=False,
            description="When True, checks recent sent emails to verify daily quota headroom before planning.",
        ),
        compact: bool = Field(
            default=True,
            description="When True, returns a compact preview summary with 2 sample items instead of large full-plan dumps.",
        ),
    ) -> dict[str, Any]:
        return await gmail_prepare_bulk_send(
            shared_client,
            recipients=recipients,
            recipient_source=recipient_source,
            field_mapping=field_mapping,
            attachment_pattern=attachment_pattern,
            template_id=template_id,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            attachments=attachments,
            personalize=personalize,
            dry_run=dry_run,
            reconcile_sent_mail=reconcile_sent_mail,
            compact=compact,
        )

    @mcp.tool(
        name="gmail_execute_bulk_send",
        description=(
            "Phase 2 of two-phase bulk sending: Execute an approved bulk sending plan by its `plan_id`. "
            "Safety confirmation gate: Requires `confirmation_phrase='CONFIRM_SEND_<plan_id>'` to prevent accidental transmissions. "
            "Executes sends concurrently with rate limiting, quota tracking, error recovery, and optional background execution."
        ),
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_execute_bulk_send(
        plan_id: str = Field(
            ...,
            description="Plan ID generated during Phase 1 (`gmail_prepare_bulk_send`).",
        ),
        confirmation_phrase: str = Field(
            ...,
            description="Exact confirmation phrase: 'CONFIRM_SEND_<plan_id>' required to unlock execution.",
        ),
        modified_items: list[dict[str, Any]] | None = Field(
            default=None,
            description="Optional item overrides or modifications to apply to staged plan items before dispatch.",
        ),
        concurrency: int = Field(
            default=5,
            description="Number of concurrent dispatch workers (1-10). Default is 5.",
        ),
        compact: bool = Field(
            default=True,
            description="When True, returns a compact execution summary instead of exhaustive individual result arrays.",
        ),
        run_async: bool = Field(
            default=False,
            description="When True, launches bulk sending in the background and returns a job_id for monitoring.",
        ),
    ) -> dict[str, Any]:
        return await gmail_execute_bulk_send(
            shared_client,
            plan_id=plan_id,
            confirmation_phrase=confirmation_phrase,
            modified_items=modified_items,
            concurrency=concurrency,
            compact=compact,
            run_async=run_async,
        )

    @mcp.tool(
        name="gmail_bulk_send_drafts",
        description=(
            "Send a list of existing drafts directly using Gmail API's native `drafts.send` with parallel concurrency. "
            "Ideal for dispatching drafts created with `gmail_bulk_create_drafts` after user review. "
            "Supports concurrency (default 5 workers), compact return mode, and optional background execution (`run_async=True`)."
        ),
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_bulk_send_drafts(
        draft_ids: list[str] = Field(
            ...,
            description="List of Gmail draft IDs to send.",
        ),
        concurrency: int = Field(
            default=5,
            description="Number of parallel dispatch workers (1-10). Default is 5.",
        ),
        compact: bool = Field(
            default=True,
            description="When True, returns compact summary metadata and sample items rather than exhaustive lists.",
        ),
        run_async: bool = Field(
            default=False,
            description="When True, spawns sending as a background task and returns a job_id immediately.",
        ),
    ) -> dict[str, Any]:
        return await gmail_bulk_send_drafts(
            shared_client,
            draft_ids=draft_ids,
            concurrency=concurrency,
            compact=compact,
            run_async=run_async,
        )

    @mcp.tool(
        name="gmail_get_bulk_status",
        description=(
            "Check the real-time execution status and progress metrics of a background bulk send or draft send operation. "
            "Returns status ('RUNNING', 'COMPLETED', 'PARTIAL_FAILURE', 'FAILED'), processed counts, sent/failed metrics, and elapsed time."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_get_bulk_status(
        job_id: str = Field(
            ...,
            description="The unique bulk job ID returned by `gmail_execute_bulk_send` or `gmail_bulk_send_drafts` when `run_async=True`.",
        ),
    ) -> dict[str, Any]:
        return await gmail_get_bulk_status(
            shared_client,
            job_id=job_id,
        )

    @mcp.tool(
        name="gmail_prepare_bulk_reply",
        description=(
            "Phase 1 of two-phase bulk replying: Prepare personalized replies across multiple conversation threads (1..100+). "
            "Validates thread accessibility, renders template variables, and stages a bulk reply plan returning a unique `plan_id`. "
            "Does NOT send any replies. Call `gmail_execute_bulk_reply` to execute."
        ),
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_prepare_bulk_reply(
        replies: list[dict[str, Any]] = Field(
            ...,
            description="List of reply specifications, each containing 'thread_id' and optional 'body_text', 'variables'.",
        ),
        template_id: str | None = Field(
            default=None,
            description="Optional saved template ID to render reply texts.",
        ),
        attachments: list[str] | None = Field(
            default=None,
            description="Common attachment file paths to include with each reply.",
        ),
        dry_run: bool = Field(
            default=True,
            description="When True, validates inputs without persisting the plan to SQLite.",
        ),
    ) -> dict[str, Any]:
        return await gmail_prepare_bulk_reply(
            shared_client,
            replies=replies,
            template_id=template_id,
            attachments=attachments,
            dry_run=dry_run,
        )

    @mcp.tool(
        name="gmail_execute_bulk_reply",
        description=(
            "Phase 2 of two-phase bulk replying: Dispatch an approved bulk reply plan by its `plan_id`. "
            "Safety confirmation gate: Requires `confirmation_phrase='CONFIRM_REPLY_<plan_id>'` to prevent accidental dispatch."
        ),
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_execute_bulk_reply(
        plan_id: str = Field(
            ...,
            description="Plan ID generated during Phase 1 (`gmail_prepare_bulk_reply`).",
        ),
        confirmation_phrase: str = Field(
            ...,
            description="Exact confirmation phrase: 'CONFIRM_REPLY_<plan_id>' required to unlock execution.",
        ),
    ) -> dict[str, Any]:
        return await gmail_execute_bulk_reply(
            shared_client,
            plan_id=plan_id,
            confirmation_phrase=confirmation_phrase,
        )

    # -------------------------------------------------------------------------
    # F. SQLite-Backed Template Management (6 Tools)
    # -------------------------------------------------------------------------

    @mcp.tool(
        name="gmail_create_template",
        description=(
            "Create and persist a reusable email template in the local SQLite database. "
            "Templates support variable placeholders like `{first_name}`, `{company}`, `{role}` in both subject and body. "
            "Enables version-tracked, standardized messaging for outreach and repetitive communication."
        ),
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_create_template(
        name: str = Field(
            ...,
            description="Human-readable name of the template (e.g. 'Software Engineer Job Application').",
        ),
        subject: str = Field(
            ...,
            description="Template subject line with optional placeholders (e.g. 'Application: {role} - {applicant_name}').",
        ),
        template_id: str | None = Field(
            default=None,
            description="Optional unique identifier string for the template. If omitted, a slug is automatically generated.",
        ),
        body_text: str | None = Field(
            default=None,
            description="Plain text body template supporting {variable} placeholders.",
        ),
        body_html: str | None = Field(
            default=None,
            description="Optional HTML formatted body template supporting {variable} placeholders.",
        ),
        variables: list[str] | None = Field(
            default=None,
            description="Optional list of declared required variable names (e.g. ['first_name', 'role', 'company']).",
        ),
        defaults: dict[str, str] | None = Field(
            default=None,
            description="Optional default fallback values for template variables.",
        ),
        attachments: list[str] | None = Field(
            default=None,
            description="Optional list of default attachment file paths to link to this template.",
        ),
    ) -> dict[str, Any]:
        return await gmail_create_template(
            shared_client,
            name=name,
            subject=subject,
            template_id=template_id,
            body_text=body_text,
            body_html=body_html,
            variables=variables,
            defaults=defaults,
            attachments=attachments,
        )

    @mcp.tool(
        name="gmail_get_template",
        description=(
            "Retrieve a stored email template by its `template_id`, including subject line, plain text and HTML bodies, "
            "declared variables, default values, and version history."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_get_template(
        template_id: str = Field(
            ...,
            description="Unique identifier of the template to retrieve.",
        ),
        version: int | None = Field(
            default=None,
            description="Specific historical version number to retrieve. If omitted, returns the latest active version.",
        ),
    ) -> dict[str, Any]:
        return await gmail_get_template(shared_client, template_id=template_id, version=version)

    @mcp.tool(
        name="gmail_list_templates",
        description=(
            "List all reusable email templates stored in the local SQLite database. "
            "Returns template IDs, names, subjects, current versions, and modification timestamps."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_list_templates() -> dict[str, Any]:
        return await gmail_list_templates(shared_client)

    @mcp.tool(
        name="gmail_update_template",
        description=(
            "Update an existing email template in SQLite. Modifying the subject or body automatically "
            "increments the template's version counter while preserving version history in the database."
        ),
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_update_template(
        template_id: str = Field(
            ...,
            description="ID of the template to update.",
        ),
        name: str | None = Field(
            default=None,
            description="Updated human-readable name.",
        ),
        subject: str | None = Field(
            default=None,
            description="Updated subject template string.",
        ),
        body_text: str | None = Field(
            default=None,
            description="Updated plain text body template.",
        ),
        body_html: str | None = Field(
            default=None,
            description="Updated HTML body template.",
        ),
        variables: list[str] | None = Field(
            default=None,
            description="Updated list of declared variable names.",
        ),
        defaults: dict[str, str] | None = Field(
            default=None,
            description="Updated default variable fallback values.",
        ),
        attachments: list[str] | None = Field(
            default=None,
            description="Updated list of attachment file paths.",
        ),
    ) -> dict[str, Any]:
        return await gmail_update_template(
            shared_client,
            template_id=template_id,
            name=name,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            variables=variables,
            defaults=defaults,
            attachments=attachments,
        )

    @mcp.tool(
        name="gmail_delete_template",
        description=(
            "Delete an email template from the local SQLite database by its `template_id`. "
            "Safety note: This action permanently removes the template and its version history."
        ),
        annotations=DESTRUCTIVE_ANNOTATIONS,
    )
    async def _tool_gmail_delete_template(
        template_id: str = Field(
            ...,
            description="ID of the template to permanently delete.",
        ),
    ) -> dict[str, Any]:
        return await gmail_delete_template(shared_client, template_id=template_id)

    @mcp.tool(
        name="gmail_preview_template",
        description=(
            "Render an email template with supplied variable values without saving or creating drafts. "
            "Validates that all required placeholders are satisfied and detects any missing variables."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_preview_template(
        template_id: str = Field(
            ...,
            description="ID of the template to preview.",
        ),
        variables: dict[str, Any] | None = Field(
            default=None,
            description="Dictionary of variable key-values (e.g. {'role': 'Backend Engineer', 'company': 'Stripe'}).",
        ),
        version: int | None = Field(
            default=None,
            description="Optional specific version of the template to render.",
        ),
    ) -> dict[str, Any]:
        return await gmail_preview_template(
            shared_client,
            template_id=template_id,
            variables=variables,
            version=version,
        )

    # -------------------------------------------------------------------------
    # G. MIME & Attachment Handling (2 Tools)
    # -------------------------------------------------------------------------

    @mcp.tool(
        name="gmail_list_attachments",
        description=(
            "List all file attachments belonging to a specific email message by its `message_id`. "
            "Returns each attachment's unique `attachment_id`, filename, MIME content type, and byte size. "
            "Use this tool to find attachment IDs prior to calling `gmail_get_attachment`."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_list_attachments(
        message_id: str = Field(
            ...,
            description="Gmail message ID to inspect for attachments.",
        ),
    ) -> dict[str, Any]:
        return await gmail_list_attachments(shared_client, message_id=message_id)

    @mcp.tool(
        name="gmail_attach_files_to_message",
        description=(
            "Construct a MIME multipart email message containing attached local files, or append files onto an existing draft. "
            "Validates file existence, enforces 25MB total size ceiling, and strictly verifies directory boundary security."
        ),
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_attach_files_to_message(
        attachments: list[str] = Field(
            ...,
            description="List of local file paths to attach (must resolve inside allowed project directories).",
        ),
        to: str | None = Field(
            default=None,
            description="Recipient email address.",
        ),
        subject: str = Field(
            default="",
            description="Email subject line.",
        ),
        body_text: str | None = Field(
            default=None,
            description="Plain text body content.",
        ),
        body_html: str | None = Field(
            default=None,
            description="Optional HTML formatted body content.",
        ),
        draft_id: str | None = Field(
            default=None,
            description="Optional existing draft ID to append attachments onto.",
        ),
        create_draft_in_gmail: bool = Field(
            default=False,
            description="When True, creates or updates a draft in Gmail with the attached files.",
        ),
    ) -> dict[str, Any]:
        return await gmail_attach_files_to_message(
            shared_client,
            attachments=attachments,
            to=to,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            draft_id=draft_id,
            create_draft_in_gmail=create_draft_in_gmail,
        )

    # -------------------------------------------------------------------------
    # H. Labels and Mailbox Organization (8 Tools)
    # -------------------------------------------------------------------------

    @mcp.tool(
        name="gmail_list_labels",
        description=(
            "List all Gmail labels in the account, including standard system labels "
            "(INBOX, UNREAD, STARRED, IMPORTANT, SENT, DRAFT, SPAM, TRASH) and custom user labels. "
            "Returns label IDs, names, types, and message counts."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_list_labels() -> dict[str, Any]:
        return await gmail_list_labels(shared_client)

    @mcp.tool(
        name="gmail_create_label",
        description=(
            "Create a new custom user label in Gmail. Supports nested hierarchical label paths "
            "(e.g. 'Job Applications/Interviewing') and optional UI background/text color styling."
        ),
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_create_label(
        label_name: str = Field(
            ...,
            description="Name for the new label (e.g. 'Job Applications', 'Clients/Acme').",
        ),
        label_list_visibility: Literal["labelShow", "labelShowIfUnread", "labelHide"] = Field(
            default="labelShow",
            description="Sidebar visibility: 'labelShow', 'labelShowIfUnread', or 'labelHide'.",
        ),
        message_list_visibility: Literal["show", "hide"] = Field(
            default="show",
            description="Message list badge visibility: 'show' or 'hide'.",
        ),
        background_color: str | None = Field(
            default=None,
            description="Optional hex background color for label badge in Gmail UI.",
        ),
        text_color: str | None = Field(
            default=None,
            description="Optional hex text color for label badge in Gmail UI.",
        ),
    ) -> dict[str, Any]:
        return await gmail_create_label(
            shared_client,
            label_name=label_name,
            label_list_visibility=label_list_visibility,
            message_list_visibility=message_list_visibility,
            background_color=background_color,
            text_color=text_color,
        )

    @mcp.tool(
        name="gmail_update_label",
        description=(
            "Modify properties of an existing user-created label (rename label, change list visibility, or update colors). "
            "Safety protection: System labels (e.g. INBOX, SENT) cannot be modified or renamed."
        ),
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_update_label(
        label_id: str = Field(
            ...,
            description="Unique Gmail ID of the label to update.",
        ),
        new_name: str | None = Field(
            default=None,
            description="Updated name for the label.",
        ),
        label_list_visibility: Literal["labelShow", "labelShowIfUnread", "labelHide"] | None = Field(
            default=None,
            description="Updated sidebar list visibility.",
        ),
        message_list_visibility: Literal["show", "hide"] | None = Field(
            default=None,
            description="Updated message list badge visibility.",
        ),
        background_color: str | None = Field(
            default=None,
            description="Updated hex background color.",
        ),
        text_color: str | None = Field(
            default=None,
            description="Updated hex text color.",
        ),
    ) -> dict[str, Any]:
        return await gmail_update_label(
            shared_client,
            label_id=label_id,
            new_name=new_name,
            label_list_visibility=label_list_visibility,
            message_list_visibility=message_list_visibility,
            background_color=background_color,
            text_color=text_color,
        )

    @mcp.tool(
        name="gmail_delete_label",
        description=(
            "Permanently delete a custom user label by its `label_id`. "
            "Emails tagged with this label remain in the mailbox; only the label tag is removed. "
            "Safety protection: System labels (INBOX, SENT, TRASH, etc.) cannot be deleted."
        ),
        annotations=DESTRUCTIVE_ANNOTATIONS,
    )
    async def _tool_gmail_delete_label(
        label_id: str = Field(
            ...,
            description="ID of the user-created label to delete.",
        ),
    ) -> dict[str, Any]:
        return await gmail_delete_label(shared_client, label_id=label_id)

    @mcp.tool(
        name="gmail_apply_label",
        description=(
            "Apply one or more Gmail labels to target messages or conversation threads. "
            "Accepts either explicit label IDs or human-readable label names (auto-resolved)."
        ),
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_apply_label(
        message_ids: list[str] | None = Field(
            default=None,
            description="List of message IDs to apply the label(s) onto.",
        ),
        thread_ids: list[str] | None = Field(
            default=None,
            description="List of conversation thread IDs to apply the label(s) onto.",
        ),
        label_ids: list[str] | None = Field(
            default=None,
            description="List of Gmail label IDs to add.",
        ),
        label_name: str | None = Field(
            default=None,
            description="Human-readable label name (e.g. 'Job Applications') to apply; resolved automatically to label ID.",
        ),
    ) -> dict[str, Any]:
        return await gmail_apply_label(
            shared_client,
            message_ids=message_ids,
            thread_ids=thread_ids,
            label_ids=label_ids,
            label_name=label_name,
        )

    @mcp.tool(
        name="gmail_remove_label",
        description=(
            "Remove one or more Gmail labels from target messages or conversation threads. "
            "Accepts either explicit label IDs or human-readable label names."
        ),
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_remove_label(
        message_ids: list[str] | None = Field(
            default=None,
            description="List of message IDs to remove the label(s) from.",
        ),
        thread_ids: list[str] | None = Field(
            default=None,
            description="List of conversation thread IDs to remove the label(s) from.",
        ),
        label_ids: list[str] | None = Field(
            default=None,
            description="List of Gmail label IDs to remove.",
        ),
        label_name: str | None = Field(
            default=None,
            description="Human-readable label name to remove; resolved automatically to label ID.",
        ),
    ) -> dict[str, Any]:
        return await gmail_remove_label(
            shared_client,
            message_ids=message_ids,
            thread_ids=thread_ids,
            label_ids=label_ids,
            label_name=label_name,
        )

    @mcp.tool(
        name="gmail_bulk_label_messages",
        description=(
            "Efficiently apply and/or remove labels across large sets of messages (up to 1,000+) using Gmail batchModify. "
            "Executes in chunks of 1,000 with automatic rate limiting and error recovery."
        ),
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_bulk_label_messages(
        message_ids: list[str] = Field(
            ...,
            description="List of message IDs (up to 1,000+ items) to modify in bulk.",
        ),
        add_label_ids: list[str] | None = Field(
            default=None,
            description="List of label IDs to add to all specified messages.",
        ),
        add_label_names: list[str] | None = Field(
            default=None,
            description="List of human-readable label names to add (auto-resolved).",
        ),
        remove_label_ids: list[str] | None = Field(
            default=None,
            description="List of label IDs to remove from all specified messages.",
        ),
        remove_label_names: list[str] | None = Field(
            default=None,
            description="List of human-readable label names to remove (auto-resolved).",
        ),
        chunk_size: int = Field(
            default=1000,
            ge=1,
            le=1000,
            description="Batch chunk size for Gmail API requests (default 1000).",
        ),
    ) -> dict[str, Any]:
        return await gmail_bulk_label_messages(
            shared_client,
            message_ids=message_ids,
            add_label_ids=add_label_ids,
            add_label_names=add_label_names,
            remove_label_ids=remove_label_ids,
            remove_label_names=remove_label_names,
            chunk_size=chunk_size,
        )

    @mcp.tool(
        name="gmail_modify_messages",
        description=(
            "Perform standard mailbox triage actions on messages or threads: "
            "'mark_read', 'mark_unread', 'archive' (remove from INBOX), 'unarchive' (return to INBOX), "
            "'star', 'unstar', 'trash', or 'untrash'. "
            "Requires confirmation for permanent delete actions."
        ),
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_modify_messages(
        action: Literal[
            "mark_read",
            "mark_unread",
            "archive",
            "unarchive",
            "star",
            "unstar",
            "trash",
            "untrash",
            "permanent_delete",
        ] = Field(
            ...,
            description="Triage action: 'mark_read', 'mark_unread', 'archive', 'unarchive', 'star', 'unstar', 'trash', 'untrash', or 'permanent_delete'.",
        ),
        message_ids: list[str] | None = Field(
            default=None,
            description="List of message IDs to modify.",
        ),
        thread_ids: list[str] | None = Field(
            default=None,
            description="List of conversation thread IDs to modify.",
        ),
        dry_run: bool = Field(
            default=False,
            description="When True, previews the modifications without changing mailbox state.",
        ),
        confirm_permanent_delete: bool | None = Field(
            default=None,
            description="Safety confirmation: Must be explicitly True if action is 'permanent_delete'.",
        ),
    ) -> dict[str, Any]:
        return await gmail_modify_messages(
            shared_client,
            action=action,
            message_ids=message_ids,
            thread_ids=thread_ids,
            dry_run=dry_run,
            confirm_permanent_delete=confirm_permanent_delete,
        )

    # -------------------------------------------------------------------------
    # I. Mailbox Settings and Filters (6 Tools)
    # -------------------------------------------------------------------------

    @mcp.tool(
        name="gmail_get_mailbox_settings",
        description=(
            "Retrieve current Gmail mailbox configuration, including out-of-office vacation auto-responder status, "
            "verified send-as email aliases, automatic forwarding addresses, and POP/IMAP protocol settings."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_get_mailbox_settings() -> dict[str, Any]:
        return await gmail_get_mailbox_settings(shared_client)

    @mcp.tool(
        name="gmail_update_vacation_settings",
        description=(
            "Configure or update Gmail out-of-office vacation auto-responder settings. "
            "Safety confirmation gate: Requires `confirm_update=True` to apply changes."
        ),
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_update_vacation_settings(
        enable_auto_reply: bool = Field(
            ...,
            description="Set to True to turn ON the vacation responder, or False to turn it OFF.",
        ),
        response_subject: str | None = Field(
            default=None,
            description="Subject line for the out-of-office auto-reply message.",
        ),
        response_body_plain_text: str | None = Field(
            default=None,
            description="Plain text content of the auto-reply message.",
        ),
        response_body_html: str | None = Field(
            default=None,
            description="Optional HTML formatted auto-reply content.",
        ),
        restrict_to_contacts: bool = Field(
            default=False,
            description="When True, only send auto-replies to contacts in your Google Contacts.",
        ),
        restrict_to_domain: bool = Field(
            default=False,
            description="When True, only send auto-replies to users within the same Google Workspace domain.",
        ),
        start_time_epoch_ms: int | None = Field(
            default=None,
            description="Start date/time in epoch milliseconds.",
        ),
        end_time_epoch_ms: int | None = Field(
            default=None,
            description="End date/time in epoch milliseconds.",
        ),
        confirm_update: bool = Field(
            default=False,
            description="Explicit confirmation gate: Must be True to update vacation settings.",
        ),
    ) -> dict[str, Any]:
        return await gmail_update_vacation_settings(
            shared_client,
            enable_auto_reply=enable_auto_reply,
            response_subject=response_subject,
            response_body_plain_text=response_body_plain_text,
            response_body_html=response_body_html,
            restrict_to_contacts=restrict_to_contacts,
            restrict_to_domain=restrict_to_domain,
            start_time_epoch_ms=start_time_epoch_ms,
            end_time_epoch_ms=end_time_epoch_ms,
            confirm_update=confirm_update,
        )

    @mcp.tool(
        name="gmail_list_filters",
        description=(
            "List all automated Gmail filtering rules currently active on the account, "
            "showing match criteria (sender, recipient, subject, query) and actions (apply label, archive, star, forward, trash)."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_list_filters() -> dict[str, Any]:
        return await gmail_list_filters(shared_client)

    @mcp.tool(
        name="gmail_get_filter",
        description="Retrieve exact matching criteria and automated actions of a specific Gmail filter rule by its `filter_id`.",
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_get_filter(
        filter_id: str = Field(
            ...,
            description="Gmail filter ID to retrieve.",
        ),
    ) -> dict[str, Any]:
        return await gmail_get_filter(shared_client, filter_id=filter_id)

    @mcp.tool(
        name="gmail_create_filter",
        description=(
            "Create a new automated Gmail filtering rule that triggers actions on incoming messages matching given criteria. "
            "Criteria examples: `{'from': 'news@site.com', 'hasAttachment': True}`. "
            "Action examples: `{'addLabelIds': ['Label_123'], 'removeLabelIds': ['INBOX']}`. "
            "Forwarding actions require `confirm_forward=True`."
        ),
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_create_filter(
        criteria: dict[str, Any] = Field(
            ...,
            description="Filter criteria dictionary (e.g. {'from': 'alerts@site.com', 'subject': 'URGENT'}).",
        ),
        actions: dict[str, Any] = Field(
            ...,
            description="Filter actions dictionary (e.g. {'addLabelIds': ['Label_1'], 'removeLabelIds': ['INBOX']}).",
        ),
        confirm_forward: bool = Field(
            default=False,
            description="Safety confirmation: Must be True if actions include automated email forwarding.",
        ),
    ) -> dict[str, Any]:
        return await gmail_create_filter(
            shared_client,
            criteria=criteria,
            actions=actions,
            confirm_forward=confirm_forward,
        )

    @mcp.tool(
        name="gmail_delete_filter",
        description=(
            "Permanently delete an automated Gmail filtering rule by its `filter_id`. "
            "Safety note: This action cannot be undone."
        ),
        annotations=DESTRUCTIVE_ANNOTATIONS,
    )
    async def _tool_gmail_delete_filter(
        filter_id: str = Field(
            ...,
            description="ID of the Gmail filter rule to delete.",
        ),
    ) -> dict[str, Any]:
        return await gmail_delete_filter(shared_client, filter_id=filter_id)

    # -------------------------------------------------------------------------
    # J. Scheduled Sending & Synchronization (7 Tools)
    # -------------------------------------------------------------------------

    @mcp.tool(
        name="gmail_schedule_send",
        description=(
            "Schedule an email for future delivery at a specified timestamp. "
            "Stored in the local SQLite database and executed automatically by the background scheduler worker when due. "
            "Supports recipient, subject, plain text/HTML body, templates, variables, attachments, and threading."
        ),
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_schedule_send(
        recipient: str = Field(
            ...,
            description="Recipient email address.",
        ),
        scheduled_at: str = Field(
            ...,
            description="Future delivery time formatted as an ISO 8601 string (e.g. '2026-11-15T09:00:00Z').",
        ),
        template_id: str | None = Field(
            default=None,
            description="Optional SQLite template ID to render subject and body.",
        ),
        subject: str | None = Field(
            default=None,
            description="Subject line if template_id is omitted.",
        ),
        body_text: str | None = Field(
            default=None,
            description="Plain text body if template_id is omitted.",
        ),
        body_html: str | None = Field(
            default=None,
            description="Optional HTML body.",
        ),
        variables: dict[str, Any] | None = Field(
            default=None,
            description="Template variables dictionary.",
        ),
        cc: str | list[str] | None = Field(
            default=None,
            description="Optional CC recipient(s).",
        ),
        bcc: str | list[str] | None = Field(
            default=None,
            description="Optional BCC recipient(s).",
        ),
        reply_to: str | None = Field(
            default=None,
            description="Optional custom Reply-To header.",
        ),
        attachments: list[str] | None = Field(
            default=None,
            description="List of local file paths to attach.",
        ),
        thread_id: str | None = Field(
            default=None,
            description="Optional thread ID if scheduling a reply in an existing conversation.",
        ),
        idempotency_key: str | None = Field(
            default=None,
            description="Optional client idempotency token.",
        ),
    ) -> dict[str, Any]:
        return await gmail_schedule_send(
            shared_client,
            recipient=recipient,
            scheduled_at=scheduled_at,
            template_id=template_id,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            variables=variables,
            cc=cc,
            bcc=bcc,
            reply_to=reply_to,
            attachments=attachments,
            thread_id=thread_id,
            idempotency_key=idempotency_key,
        )

    @mcp.tool(
        name="gmail_list_scheduled_sends",
        description=(
            "List scheduled future email jobs stored in SQLite, optionally filtered by job status "
            "('pending', 'sent', 'failed', 'cancelled')."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_list_scheduled_sends(
        status: Literal["pending", "sent", "failed", "cancelled"] | None = Field(
            default=None,
            description="Filter by job status: 'pending', 'sent', 'failed', or 'cancelled'. If omitted, returns all scheduled jobs.",
        ),
    ) -> dict[str, Any]:
        return await gmail_list_scheduled_sends(shared_client, status=status)

    @mcp.tool(
        name="gmail_cancel_scheduled_send",
        description="Cancel a pending scheduled email job by its `job_id`, preventing it from being dispatched.",
        annotations=WRITE_IDEMPOTENT_ANNOTATIONS,
    )
    async def _tool_gmail_cancel_scheduled_send(
        job_id: str = Field(
            ...,
            description="ID of the scheduled email job to cancel.",
        ),
    ) -> dict[str, Any]:
        return await gmail_cancel_scheduled_send(shared_client, job_id=job_id)

    @mcp.tool(
        name="gmail_process_due_scheduled_sends",
        description=(
            "Immediately trigger evaluation and transmission of all scheduled emails whose scheduled delivery time "
            "has arrived or passed, without waiting for the background scheduler interval."
        ),
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_process_due_scheduled_sends() -> dict[str, Any]:
        return await gmail_process_due_scheduled_sends(shared_client)

    @mcp.tool(
        name="gmail_start_watch",
        description=(
            "Set up a Gmail push notification watch on a Google Cloud Pub/Sub topic for real-time mailbox event alerts. "
            "Returns expiration timestamp and initial mailbox historyId."
        ),
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_start_watch(
        topic_name: str | None = Field(
            default=None,
            description="Full Google Cloud Pub/Sub topic name (e.g. 'projects/my-gcp-project/topics/gmail-events').",
        ),
        label_ids: list[str] | None = Field(
            default=None,
            description="Optional list of label IDs to filter push notifications for (e.g. ['INBOX', 'UNREAD']).",
        ),
        label_filter_action: Literal["include", "exclude"] = Field(
            default="include",
            description="Filter behavior: 'include' (only notify for these labels) or 'exclude'.",
        ),
    ) -> dict[str, Any]:
        return await gmail_start_watch(
            shared_client,
            topic_name=topic_name,
            label_ids=label_ids,
            label_filter_action=label_filter_action,
        )

    @mcp.tool(
        name="gmail_sync_history",
        description=(
            "Synchronize incremental mailbox changes via Gmail `history.list` since a given `start_history_id`. "
            "Detects newly added messages, deleted messages, and label additions/removals with Pub/Sub deduplication. "
            "Falls back to full resync if the history ID has expired on Google servers."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_sync_history(
        start_history_id: str | None = Field(
            default=None,
            description="Starting history ID to sync changes from. If omitted, uses the stored sync checkpoint.",
        ),
        notification_id: str | None = Field(
            default=None,
            description="Optional Pub/Sub message ID used for event deduplication.",
        ),
        label_id: str | None = Field(
            default=None,
            description="Optional label ID to restrict history event processing.",
        ),
        history_types: list[Literal["messageAdded", "messageDeleted", "labelAdded", "labelRemoved"]] | None = Field(
            default=None,
            description="Optional filter for specific change types (e.g. ['messageAdded', 'labelAdded']).",
        ),
        max_results: int = Field(
            default=500,
            ge=1,
            le=1000,
            description="Maximum number of history change records to process in this sync call (default 500).",
        ),
    ) -> dict[str, Any]:
        return await gmail_sync_history(
            shared_client,
            start_history_id=start_history_id,
            notification_id=notification_id,
            label_id=label_id,
            history_types=history_types,
            max_results=max_results,
        )

    @mcp.tool(
        name="gmail_stop_watch",
        description="Stop active Gmail push notifications on the mailbox (`users.stop`), terminating the Pub/Sub event feed.",
        annotations=WRITE_IDEMPOTENT_ANNOTATIONS,
    )
    async def _tool_gmail_stop_watch() -> dict[str, Any]:
        return await gmail_stop_watch(shared_client)

    return mcp, shared_client


async def _run_self_check() -> dict[str, Any]:
    mcp, client = create_gmail_mcp_server(start_scheduler=False)
    tools = await mcp.list_tools()
    health = await gmail_health_check(client)
    return {
        "server_name": mcp.name,
        "registered_tool_count": len(tools),
        "registered_tools": [t.name for t in tools],
        "health": health,
    }


async def dump_schemas(target_dir: Path | str | None = None) -> list[str]:
    """Export tool JSON schemas and instructions.md into target directory."""
    if target_dir is None:
        target_dir = Path.home() / ".gemini" / "antigravity-cli" / "mcp" / "gmail"
    dest = Path(target_dir).resolve()
    dest.mkdir(parents=True, exist_ok=True)

    mcp, _ = create_gmail_mcp_server(start_scheduler=False)
    tools = await mcp.list_tools()
    exported_files = []

    for tool in tools:
        tool_data = {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": tool.inputSchema or {},
        }
        tool_file = dest / f"{tool.name}.json"
        with open(tool_file, "w", encoding="utf-8") as f:
            json.dump(tool_data, f, indent=2)
        exported_files.append(str(tool_file))

    instructions_file = dest / "instructions.md"
    instructions_content = (
        "# Gmail Automation MCP Server Instructions\n\n"
        "Production-grade Gmail Automation MCP Server using official Gmail REST API v1.\n"
        "Provides 60 fine-grained tools for search, retrieval, drafting, sending, templates, attachments, "
        "labels, mailbox settings, filters, and scheduling.\n\n"
        "## Key Workflow Patterns\n"
        "1. **Reconnaissance & Auth**: Use `gmail_get_profile` and `gmail_auth_status` to verify user and token readiness.\n"
        "2. **Finding Emails**: Use `gmail_search_messages` with Gmail syntax (`is:unread`, `from:...`). Returns fast metadata.\n"
        "3. **Reading Email Content**: Pass the `id` from search into `gmail_get_message` to retrieve full plain text and HTML bodies.\n"
        "4. **Drafting (Safe & Non-Destructive)**: Use `gmail_create_draft` or `gmail_bulk_create_drafts` to stage emails for user review.\n"
        "5. **Sending (Safety Confirmed)**: Use `gmail_send_email` (single) or `gmail_prepare_bulk_send` -> `gmail_execute_bulk_send` (bulk). "
        "Requires explicit confirmation flags (`confirm_send=True` or `CONFIRM_SEND_<plan_id>`).\n"
        "6. **Replying & Threading**: Use `gmail_send_reply` or `gmail_send_reply_all` with `thread_id` and `message_id`.\n"
    )
    with open(instructions_file, "w", encoding="utf-8") as f:
        f.write(instructions_content)
    exported_files.append(str(instructions_file))

    return exported_files


def main() -> int:
    parser = argparse.ArgumentParser(description="Gmail Automation MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
        help="MCP transport protocol (default: stdio)",
    )
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="Verify server initialization, tool registration, and SQLite/OAuth health, then exit",
    )
    parser.add_argument(
        "--dump-schemas",
        nargs="?",
        const="",
        default=None,
        help="Export all 60 tool JSON schemas and instructions.md into target directory (default: ~/.gemini/antigravity-cli/mcp/gmail)",
    )
    parser.add_argument(
        "--ignore-lock",
        action="store_true",
        help="Bypass singleton server instance check (default: False)",
    )
    args = parser.parse_args()

    if args.self_check:
        report = asyncio.run(_run_self_check())
        print(json.dumps(report, indent=2))
        return 0

    if args.dump_schemas is not None:
        target = Path(args.dump_schemas) if args.dump_schemas else None
        exported = asyncio.run(dump_schemas(target))
        print(f"Exported {len(exported)} schema files successfully.")
        return 0

    # Singleton instance guard: prevent multiple server processes
    server_lock = None
    if not args.ignore_lock:
        from gmail_mcp.core.lock import SingleInstanceLock
        server_lock = SingleInstanceLock()
        if not server_lock.acquire(transport=args.transport):
            info = server_lock.get_running_instance_info()
            pid_str = f" (PID: {info.get('pid')}, started: {info.get('started_at')})" if info and "pid" in info else ""
            sys.stderr.write(
                f"[GMAIL_MCP_ERROR] Another instance of Gmail MCP Server is already running{pid_str}.\n"
                f"Cannot launch multiple server instances simultaneously. Exiting.\n"
            )
            sys.stderr.flush()
            return 1

    try:
        mcp, _ = create_gmail_mcp_server(start_scheduler=True)
        mcp.run(transport=args.transport)
        return 0
    finally:
        if server_lock:
            server_lock.release()


if __name__ == "__main__":
    sys.exit(main())
