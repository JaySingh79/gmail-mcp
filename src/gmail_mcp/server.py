"""FastMCP server registration, lifespan dependency injection, and CLI entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

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
    All 42 tools share the same `GmailClientWrapper`, `QuotaManager`, `RateLimiter`, and `DatabaseManager`.
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
        description="Return authenticated email address, mailbox profile (messagesTotal, threadsTotal), and history ID.",
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_get_profile() -> dict[str, Any]:
        return await gmail_get_profile(shared_client)

    @mcp.tool(
        name="gmail_auth_status",
        description="Check OAuth token availability, granted scopes, authentication status, and API connectivity.",
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_auth_status(verify_connectivity: bool = True) -> dict[str, Any]:
        return await gmail_auth_status(shared_client, verify_connectivity=verify_connectivity)

    @mcp.tool(
        name="gmail_get_runtime_limits",
        description=(
            "Report configured account limits, Google documented quota model vs local estimates, "
            "measured 60s/24h quota usage, and daily sending headroom."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_get_runtime_limits(reconcile_sent_mail: bool = False) -> dict[str, Any]:
        return await gmail_get_runtime_limits(shared_client, reconcile_sent_mail=reconcile_sent_mail)

    @mcp.tool(
        name="gmail_health_check",
        description="Check Gmail API connectivity, OAuth validity, SQLite database migrations, and allowed directories.",
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_health_check() -> dict[str, Any]:
        return await gmail_health_check(shared_client)

    @mcp.tool(
        name="gmail_get_mailbox_stats",
        description="Retrieve authoritative mailbox statistics and label-based message/thread counts across system and user labels.",
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_get_mailbox_stats(include_user_labels: bool = True) -> dict[str, Any]:
        return await gmail_get_mailbox_stats(shared_client, include_user_labels=include_user_labels)

    # -------------------------------------------------------------------------
    # B. Search and Email Retrieval (7 Tools)
    # -------------------------------------------------------------------------

    @mcp.tool(
        name="gmail_search_messages",
        description=(
            "Search emails server-side using Gmail query syntax (`is:unread`, `from:...`, `has:attachment`, etc.). "
            "Defaults to `format='metadata'` for low latency and never marks unread emails as read."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_search_messages(
        query: str | None = None,
        max_results: int = 20,
        page_token: str | None = None,
        format: Literal["minimal", "metadata", "full", "raw"] = "metadata",
        fetch_details: bool = True,
        auto_paginate: bool = False,
        include_spam_trash: bool = False,
        sender: str | None = None,
        recipient: str | None = None,
        subject: str | None = None,
        label: str | None = None,
        after: str | None = None,
        before: str | None = None,
        newer_than: str | None = None,
        older_than: str | None = None,
        has_attachment: bool | None = None,
        filename: str | None = None,
        is_unread: bool | None = None,
        rfc822msgid: str | None = None,
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
        description="Search conversations using Gmail query syntax and return thread identifiers and snippets.",
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_search_threads(
        query: str = "",
        max_results: int = 20,
        page_token: str | None = None,
        auto_paginate: bool = False,
        include_spam_trash: bool = False,
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
        description="Retrieve a particular email by `message_id` or `rfc822msgid` in minimal, metadata, full, or raw format.",
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_get_message(
        message_id: str | None = None,
        rfc822msgid: str | None = None,
        format: Literal["minimal", "metadata", "full", "raw"] = "full",
    ) -> dict[str, Any]:
        return await gmail_get_message(
            shared_client,
            message_id=message_id,
            rfc822msgid=rfc822msgid,
            format=format,
        )

    @mcp.tool(
        name="gmail_get_thread",
        description="Retrieve an entire conversation thread and all of its messages in minimal, metadata, or full format.",
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_get_thread(
        thread_id: str,
        format: Literal["minimal", "metadata", "full"] = "full",
    ) -> dict[str, Any]:
        return await gmail_get_thread(shared_client, thread_id=thread_id, format=format)

    @mcp.tool(
        name="gmail_list_unread",
        description=(
            "Retrieve unread emails across multiple pages (up to 500 per page) without losing results "
            "and without ever marking unread messages as read."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_list_unread(
        query: str | None = None,
        max_results_per_page: int = 500,
        max_total_results: int | None = None,
        page_token: str | None = None,
        include_body: bool = False,
        mark_as_read: bool = False,
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
        description="Download an attachment from a message into a configured allowed directory with path-traversal and size checks.",
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_get_attachment(
        message_id: str,
        attachment_id: str,
        destination_directory: str = "downloads",
        filename: str | None = None,
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
        description="Export matching emails into JSON, CSV metadata, or EML files (and optional attachments) inside an allowed directory.",
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_export_messages(
        query: str,
        export_format: Literal["json", "csv", "eml"] = "json",
        destination_directory: str = "exports",
        filename_prefix: str = "gmail_export",
        max_results: int = 50,
        include_attachments: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        async def _progress(current: int, total: int) -> None:
            if ctx is not None:
                try:
                    await ctx.report_progress(progress=current, total=total)
                except Exception:
                    pass

        return await gmail_export_messages(
            shared_client,
            query=query,
            export_format=export_format,
            destination_directory=destination_directory,
            filename_prefix=filename_prefix,
            max_results=max_results,
            include_attachments=include_attachments,
            progress_callback=_progress if ctx is not None else None,
        )

    # -------------------------------------------------------------------------
    # C. Draft Management (8 Tools)
    # -------------------------------------------------------------------------

    @mcp.tool(
        name="gmail_create_draft",
        description="Create a new Gmail draft supporting plaintext/HTML, attachments, and optional SQLite template rendering.",
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_create_draft(
        to: list[str] | str,
        subject: str | None = None,
        body_text: str | None = None,
        body_html: str | None = None,
        cc: list[str] | str | None = None,
        bcc: list[str] | str | None = None,
        reply_to: str | None = None,
        thread_id: str | None = None,
        in_reply_to: str | None = None,
        references: str | None = None,
        template_id: str | None = None,
        variables: dict[str, Any] | None = None,
        attachments: list[str] | None = None,
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
        description="Retrieve a particular draft by ID in minimal, metadata, full, or raw format.",
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_get_draft(
        draft_id: str,
        format: Literal["minimal", "metadata", "full", "raw"] = "full",
    ) -> dict[str, Any]:
        return await gmail_get_draft(shared_client, draft_id=draft_id, format=format)

    @mcp.tool(
        name="gmail_list_drafts",
        description="List existing drafts in the mailbox with pagination.",
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_list_drafts(
        query: str | None = None,
        max_results: int = 50,
        page_token: str | None = None,
        include_spam_trash: bool = False,
        auto_paginate: bool = False,
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
            "Duplicate an existing draft 1..N times via `drafts.get(format='raw')` and MIME reconstruction, "
            "preserving attachments, body formatting, and subject while applying optional `recipient_overrides`."
        ),
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_duplicate_draft(
        source_draft_id: str,
        copies: int = 1,
        recipient_overrides: list[str] | None = None,
        subject_override: str | None = None,
        preserve_thread: bool = False,
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
        description="Modify an existing draft while preserving existing attachments, recipients, subject, and body unless overridden.",
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_update_draft(
        draft_id: str,
        to: list[str] | str | None = None,
        cc: list[str] | str | None = None,
        bcc: list[str] | str | None = None,
        subject: str | None = None,
        body_text: str | None = None,
        body_html: str | None = None,
        additional_attachments: list[str] | None = None,
        preserve_existing_attachments: bool = True,
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
        description="Delete a draft by ID.",
        annotations=DESTRUCTIVE_ANNOTATIONS,
    )
    async def _tool_gmail_delete_draft(draft_id: str) -> dict[str, Any]:
        return await gmail_delete_draft(shared_client, draft_id=draft_id)

    @mcp.tool(
        name="gmail_bulk_create_drafts",
        description="Generate 1 to 100+ personalized drafts from a template or recipient records with shared and individual attachments.",
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_bulk_create_drafts(
        recipients: list[dict[str, Any] | str],
        template_id: str | None = None,
        subject: str | None = None,
        body_text: str | None = None,
        body_html: str | None = None,
        attachments: list[str] | None = None,
    ) -> dict[str, Any]:
        return await gmail_bulk_create_drafts(
            shared_client,
            recipients=recipients,
            template_id=template_id,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            attachments=attachments,
        )

    @mcp.tool(
        name="gmail_bulk_attach_to_drafts",
        description="Attach one or multiple shared or draft-specific documents to 1..100+ existing drafts without overwriting existing content.",
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_bulk_attach_to_drafts(
        draft_ids: list[str],
        attachments: list[str] | None = None,
        per_draft_attachments: dict[str, list[str]] | None = None,
    ) -> dict[str, Any]:
        return await gmail_bulk_attach_to_drafts(
            shared_client,
            draft_ids=draft_ids,
            attachments=attachments,
            per_draft_attachments=per_draft_attachments,
        )

    # -------------------------------------------------------------------------
    # D. Email Sending and Replies (8 Tools)
    # -------------------------------------------------------------------------

    @mcp.tool(
        name="gmail_send_email",
        description=(
            "Send a single email (requires `confirm_send=True`; returns preview when `confirm_send=False` or `dry_run=True`). "
            "Includes idempotency protection, BCC privacy, and ambiguous-timeout reconciliation."
        ),
        annotations=WRITE_IDEMPOTENT_ANNOTATIONS,
    )
    async def _tool_gmail_send_email(
        to: list[str] | str,
        subject: str | None = None,
        body_text: str | None = None,
        body_html: str | None = None,
        cc: list[str] | str | None = None,
        bcc: list[str] | str | None = None,
        reply_to: str | None = None,
        from_alias: str | None = None,
        custom_headers: dict[str, str] | None = None,
        template_id: str | None = None,
        variables: dict[str, Any] | None = None,
        attachments: list[str] | None = None,
        idempotency_key: str | None = None,
        confirm_send: bool = False,
        dry_run: bool = False,
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
            "Reply to a specific email inside its thread, automatically setting RFC `In-Reply-To`, "
            "`References`, `Re: <Subject>`, and `threadId`."
        ),
        annotations=WRITE_IDEMPOTENT_ANNOTATIONS,
    )
    async def _tool_gmail_send_reply(
        thread_id: str | None = None,
        message_id: str | None = None,
        body_text: str | None = None,
        body_html: str | None = None,
        template_id: str | None = None,
        variables: dict[str, Any] | None = None,
        attachments: list[str] | None = None,
        cc: list[str] | str | None = None,
        bcc: list[str] | str | None = None,
        idempotency_key: str | None = None,
        confirm_send: bool = False,
        dry_run: bool = False,
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
        description="Reply to all appropriate recipients (sender + original To/Cc minus authenticated user) inside the original thread.",
        annotations=WRITE_IDEMPOTENT_ANNOTATIONS,
    )
    async def _tool_gmail_send_reply_all(
        thread_id: str | None = None,
        message_id: str | None = None,
        body_text: str | None = None,
        body_html: str | None = None,
        template_id: str | None = None,
        variables: dict[str, Any] | None = None,
        attachments: list[str] | None = None,
        bcc: list[str] | str | None = None,
        idempotency_key: str | None = None,
        confirm_send: bool = False,
        dry_run: bool = False,
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
        description="Forward an existing email to approved recipients, preserving original content and attachments where requested.",
        annotations=WRITE_IDEMPOTENT_ANNOTATIONS,
    )
    async def _tool_gmail_forward_message(
        message_id: str,
        to: list[str] | str,
        comment_text: str = "",
        comment_html: str | None = None,
        cc: list[str] | str | None = None,
        bcc: list[str] | str | None = None,
        include_original_attachments: bool = True,
        additional_attachments: list[str] | None = None,
        idempotency_key: str | None = None,
        confirm_send: bool = False,
        dry_run: bool = False,
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

    @mcp.tool(
        name="gmail_prepare_bulk_send",
        description=(
            "Validate and prepare a two-phase bulk-sending plan (1..100+ emails). "
            "Estimates quota, checks daily sending headroom, stores SHA-256 plan in SQLite, and returns preview + confirmation phrase."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_prepare_bulk_send(
        recipients: list[dict[str, Any] | str] | None = None,
        recipient_source: str | None = None,
        template_id: str | None = None,
        subject: str | None = None,
        body_text: str | None = None,
        body_html: str | None = None,
        attachments: list[str] | None = None,
        personalize: bool = True,
        dry_run: bool = True,
        reconcile_sent_mail: bool = False,
    ) -> dict[str, Any]:
        return await gmail_prepare_bulk_send(
            shared_client,
            recipients=recipients,
            recipient_source=recipient_source,
            template_id=template_id,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            attachments=attachments,
            personalize=personalize,
            dry_run=dry_run,
            reconcile_sent_mail=reconcile_sent_mail,
        )

    @mcp.tool(
        name="gmail_execute_bulk_send",
        description=(
            "Execute a previously prepared bulk-sending plan after verifying `plan_id`, "
            "confirmation phrase (`CONFIRM SEND <N> EMAILS`), and SHA-256 plan integrity."
        ),
        annotations=WRITE_IDEMPOTENT_ANNOTATIONS,
    )
    async def _tool_gmail_execute_bulk_send(
        plan_id: str,
        confirmation_phrase: str,
        modified_items: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return await gmail_execute_bulk_send(
            shared_client,
            plan_id=plan_id,
            confirmation_phrase=confirmation_phrase,
            modified_items=modified_items,
        )

    @mcp.tool(
        name="gmail_prepare_bulk_reply",
        description=(
            "Prepare multiple threaded replies (1..100+ threads) by fetching source thread headers "
            "(`Message-ID`, `In-Reply-To`, `References`, `Subject`) and saving a SHA-256 verified plan."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_prepare_bulk_reply(
        replies: list[dict[str, Any]],
        template_id: str | None = None,
        attachments: list[str] | None = None,
        dry_run: bool = True,
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
            "Execute a previously prepared bulk-reply plan after verifying `plan_id`, "
            "`CONFIRM SEND <N> REPLIES`, and SHA-256 plan integrity."
        ),
        annotations=WRITE_IDEMPOTENT_ANNOTATIONS,
    )
    async def _tool_gmail_execute_bulk_reply(
        plan_id: str,
        confirmation_phrase: str,
    ) -> dict[str, Any]:
        return await gmail_execute_bulk_reply(
            shared_client,
            plan_id=plan_id,
            confirmation_phrase=confirmation_phrase,
        )

    # -------------------------------------------------------------------------
    # E. Template Management (6 Tools)
    # -------------------------------------------------------------------------

    @mcp.tool(
        name="gmail_create_template",
        description="Create a reusable SQLite-backed email template supporting `{{var}}` and `{{var|default}}` placeholders.",
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_create_template(
        name: str,
        subject: str,
        template_id: str | None = None,
        body_text: str | None = None,
        body_html: str | None = None,
        variables: list[str] | None = None,
        defaults: dict[str, str] | None = None,
        attachments: list[str] | None = None,
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
        description="Retrieve a stored email template by `template_id` and optional `version`.",
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_get_template(
        template_id: str,
        version: int | None = None,
    ) -> dict[str, Any]:
        return await gmail_get_template(shared_client, template_id=template_id, version=version)

    @mcp.tool(
        name="gmail_list_templates",
        description="List all reusable email templates stored in SQLite.",
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_list_templates() -> dict[str, Any]:
        return await gmail_list_templates(shared_client)

    @mcp.tool(
        name="gmail_update_template",
        description="Update an existing email template and increment its version number.",
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_update_template(
        template_id: str,
        name: str | None = None,
        subject: str | None = None,
        body_text: str | None = None,
        body_html: str | None = None,
        variables: list[str] | None = None,
        defaults: dict[str, str] | None = None,
        attachments: list[str] | None = None,
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
        description="Delete an email template from SQLite.",
        annotations=DESTRUCTIVE_ANNOTATIONS,
    )
    async def _tool_gmail_delete_template(template_id: str) -> dict[str, Any]:
        return await gmail_delete_template(shared_client, template_id=template_id)

    @mcp.tool(
        name="gmail_preview_template",
        description="Render a template with supplied variables, validating missing placeholders and escaping HTML variables.",
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_preview_template(
        template_id: str,
        variables: dict[str, Any] | None = None,
        version: int | None = None,
    ) -> dict[str, Any]:
        return await gmail_preview_template(
            shared_client,
            template_id=template_id,
            variables=variables,
            version=version,
        )

    # -------------------------------------------------------------------------
    # F. Attachment Management (2 Dedicated Tools + 2 Shared above)
    # -------------------------------------------------------------------------

    @mcp.tool(
        name="gmail_list_attachments",
        description="List attachment metadata (`attachment_id`, `filename`, `mime_type`, `size_bytes`) for a message.",
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_list_attachments(message_id: str) -> dict[str, Any]:
        return await gmail_list_attachments(shared_client, message_id=message_id)

    @mcp.tool(
        name="gmail_attach_files_to_message",
        description="Construct a MIME message with attachments (or append attachments to an existing draft) with size and path validation.",
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_attach_files_to_message(
        attachments: list[str],
        to: list[str] | str | None = None,
        subject: str = "",
        body_text: str | None = None,
        body_html: str | None = None,
        draft_id: str | None = None,
        create_draft_in_gmail: bool = False,
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
    # G. Label Management (7 Tools)
    # -------------------------------------------------------------------------

    @mcp.tool(
        name="gmail_list_labels",
        description="List all system and user-created Gmail labels.",
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_list_labels() -> dict[str, Any]:
        return await gmail_list_labels(shared_client)

    @mcp.tool(
        name="gmail_create_label",
        description="Create a user label (supports nested names like 'Job Applications/2026').",
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_create_label(
        label_name: str,
        label_list_visibility: Literal["labelShow", "labelShowIfUnread", "labelHide"] = "labelShow",
        message_list_visibility: Literal["show", "hide"] = "show",
        background_color: str | None = None,
        text_color: str | None = None,
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
        description="Modify properties of an existing user-created label.",
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_update_label(
        label_id: str,
        new_name: str | None = None,
        label_list_visibility: Literal["labelShow", "labelShowIfUnread", "labelHide"] | None = None,
        message_list_visibility: Literal["show", "hide"] | None = None,
        background_color: str | None = None,
        text_color: str | None = None,
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
        description="Delete a user-created label (system labels are protected).",
        annotations=DESTRUCTIVE_ANNOTATIONS,
    )
    async def _tool_gmail_delete_label(label_id: str) -> dict[str, Any]:
        return await gmail_delete_label(shared_client, label_id=label_id)

    @mcp.tool(
        name="gmail_apply_label",
        description="Apply one or more labels (by ID or name) to messages or threads.",
        annotations=WRITE_IDEMPOTENT_ANNOTATIONS,
    )
    async def _tool_gmail_apply_label(
        message_ids: list[str] | str | None = None,
        thread_ids: list[str] | str | None = None,
        label_ids: list[str] | str | None = None,
        label_name: list[str] | str | None = None,
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
        description="Remove one or more labels (by ID or name) from messages or threads.",
        annotations=WRITE_IDEMPOTENT_ANNOTATIONS,
    )
    async def _tool_gmail_remove_label(
        message_ids: list[str] | str | None = None,
        thread_ids: list[str] | str | None = None,
        label_ids: list[str] | str | None = None,
        label_name: list[str] | str | None = None,
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
        description="Apply or remove labels across 100 to 1,000+ messages using `messages.batchModify`.",
        annotations=WRITE_IDEMPOTENT_ANNOTATIONS,
    )
    async def _tool_gmail_bulk_label_messages(
        message_ids: list[str],
        add_label_ids: list[str] | str | None = None,
        add_label_names: list[str] | str | None = None,
        remove_label_ids: list[str] | str | None = None,
        remove_label_names: list[str] | str | None = None,
        chunk_size: int = 1000,
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

    # -------------------------------------------------------------------------
    # H. Mailbox Organization and Settings (3 Tools)
    # -------------------------------------------------------------------------

    @mcp.tool(
        name="gmail_modify_messages",
        description=(
            "Modify messages or threads: `mark_read`, `mark_unread`, `archive`, `unarchive`, `star`, `unstar`, "
            "`mark_important`, `mark_not_important`, `move_to_trash`, `restore_from_trash`, `mark_spam`, `remove_from_spam`."
        ),
        annotations=WRITE_IDEMPOTENT_ANNOTATIONS,
    )
    async def _tool_gmail_modify_messages(
        action: Literal[
            "mark_read",
            "mark_unread",
            "archive",
            "unarchive",
            "star",
            "unstar",
            "mark_important",
            "mark_not_important",
            "move_to_trash",
            "restore_from_trash",
            "mark_spam",
            "remove_from_spam",
            "permanent_delete",
        ],
        message_ids: list[str] | str | None = None,
        thread_ids: list[str] | str | None = None,
        dry_run: bool = False,
        confirm_permanent_delete: str | None = None,
    ) -> dict[str, Any]:
        return await gmail_modify_messages(
            shared_client,
            action=action,
            message_ids=message_ids,
            thread_ids=thread_ids,
            dry_run=dry_run,
            confirm_permanent_delete=confirm_permanent_delete,
        )

    @mcp.tool(
        name="gmail_get_mailbox_settings",
        description="Read vacation-responder settings, verified send-as aliases, and auto-forwarding settings.",
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_get_mailbox_settings() -> dict[str, Any]:
        return await gmail_get_mailbox_settings(shared_client)

    @mcp.tool(
        name="gmail_update_vacation_settings",
        description="Update vacation-responder settings (requires `confirm_update=True` and `gmail.settings.basic` scope).",
        annotations=WRITE_IDEMPOTENT_ANNOTATIONS,
    )
    async def _tool_gmail_update_vacation_settings(
        enable_auto_reply: bool,
        response_subject: str | None = None,
        response_body_plain_text: str | None = None,
        response_body_html: str | None = None,
        restrict_to_contacts: bool = False,
        restrict_to_domain: bool = False,
        start_time_epoch_ms: int | None = None,
        end_time_epoch_ms: int | None = None,
        confirm_update: bool = False,
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

    # -------------------------------------------------------------------------
    # I. Gmail Filters (4 Tools)
    # -------------------------------------------------------------------------

    @mcp.tool(
        name="gmail_list_filters",
        description="List all Gmail filters on the account.",
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_list_filters() -> dict[str, Any]:
        return await gmail_list_filters(shared_client)

    @mcp.tool(
        name="gmail_get_filter",
        description="Get details of a specific Gmail filter by ID.",
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_get_filter(filter_id: str) -> dict[str, Any]:
        return await gmail_get_filter(shared_client, filter_id=filter_id)

    @mcp.tool(
        name="gmail_create_filter",
        description="Create a new Gmail filter after validating matching criteria and actions.",
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_create_filter(
        criteria: dict[str, Any],
        actions: dict[str, Any],
        confirm_forward: bool = False,
    ) -> dict[str, Any]:
        return await gmail_create_filter(
            shared_client,
            criteria=criteria,
            actions=actions,
            confirm_forward=confirm_forward,
        )

    @mcp.tool(
        name="gmail_delete_filter",
        description="Delete a Gmail filter by ID.",
        annotations=DESTRUCTIVE_ANNOTATIONS,
    )
    async def _tool_gmail_delete_filter(filter_id: str) -> dict[str, Any]:
        return await gmail_delete_filter(shared_client, filter_id=filter_id)

    # -------------------------------------------------------------------------
    # J. Scheduled Sending and Incremental Synchronization (7 Tools)
    # -------------------------------------------------------------------------

    @mcp.tool(
        name="gmail_schedule_send",
        description=(
            "Schedule an email for future delivery using the local persistent SQLite scheduler. "
            "Survives restarts and respects quota and daily sending caps."
        ),
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_schedule_send(
        recipient: str | list[str],
        scheduled_at: str,
        template_id: str | None = None,
        subject: str | None = None,
        body_text: str | None = None,
        body_html: str | None = None,
        variables: dict[str, Any] | None = None,
        cc: list[str] | str | None = None,
        bcc: list[str] | str | None = None,
        reply_to: str | None = None,
        attachments: list[str] | None = None,
        thread_id: str | None = None,
        idempotency_key: str | None = None,
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
        description="List scheduled email jobs stored in SQLite (optionally filtered by status).",
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_list_scheduled_sends(status: str | None = None) -> dict[str, Any]:
        return await gmail_list_scheduled_sends(shared_client, status=status)

    @mcp.tool(
        name="gmail_cancel_scheduled_send",
        description="Cancel a pending scheduled email job by `job_id`.",
        annotations=WRITE_IDEMPOTENT_ANNOTATIONS,
    )
    async def _tool_gmail_cancel_scheduled_send(job_id: str) -> dict[str, Any]:
        return await gmail_cancel_scheduled_send(shared_client, job_id=job_id)

    @mcp.tool(
        name="gmail_process_due_scheduled_sends",
        description="Trigger immediate execution of any due scheduled emails in the SQLite queue.",
        annotations=WRITE_IDEMPOTENT_ANNOTATIONS,
    )
    async def _tool_gmail_process_due_scheduled_sends() -> dict[str, Any]:
        return await gmail_process_due_scheduled_sends(shared_client)

    @mcp.tool(
        name="gmail_start_watch",
        description="Start a Gmail push notification watch on a configured Google Cloud Pub/Sub topic.",
        annotations=WRITE_SAFE_ANNOTATIONS,
    )
    async def _tool_gmail_start_watch(
        topic_name: str | None = None,
        label_ids: list[str] | None = None,
        label_filter_action: Literal["include", "exclude"] = "include",
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
            "Synchronize incremental mailbox changes via `history.list`, deduplicating Pub/Sub notifications "
            "and falling back to full resync if the stored `history_id` has expired."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    async def _tool_gmail_sync_history(
        start_history_id: str | None = None,
        notification_id: str | None = None,
        label_id: str | None = None,
        history_types: list[Literal["messageAdded", "messageDeleted", "labelAdded", "labelRemoved"]] | None = None,
        max_results: int = 500,
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
        description="Stop active Gmail push notifications (`users.stop`).",
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
    args = parser.parse_args()

    if args.self_check:
        report = asyncio.run(_run_self_check())
        print(json.dumps(report, indent=2))
        return 0

    mcp, _ = create_gmail_mcp_server(start_scheduler=True)
    mcp.run(transport=args.transport)
    return 0


if __name__ == "__main__":
    sys.exit(main())
