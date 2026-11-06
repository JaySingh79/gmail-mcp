"""OAuth 2.0 scope definitions and permission diagnostics."""

from __future__ import annotations

from typing import Any

# Narrowest practical OAuth scopes for full personal mailbox + settings/filter management
SCOPE_GMAIL_MODIFY = "https://www.googleapis.com/auth/gmail.modify"
SCOPE_GMAIL_SETTINGS_BASIC = "https://www.googleapis.com/auth/gmail.settings.basic"
# Full mail.google.com scope is intentionally excluded from defaults (Trash is used instead of permanent delete)
SCOPE_GMAIL_FULL_ACCESS = "https://mail.google.com/"

DEFAULT_SCOPES: list[str] = [
    SCOPE_GMAIL_MODIFY,
    SCOPE_GMAIL_SETTINGS_BASIC,
]

SCOPE_DESCRIPTIONS: dict[str, str] = {
    SCOPE_GMAIL_MODIFY: (
        "Read, compose, send, draft, label, archive, and move messages/threads to Trash "
        "(all core mailbox operations except permanent deletion and filter/settings management)."
    ),
    SCOPE_GMAIL_SETTINGS_BASIC: (
        "Manage Gmail filters (list, get, create, delete), read/update vacation responder settings, "
        "and inspect verified send-as aliases."
    ),
    SCOPE_GMAIL_FULL_ACCESS: (
        "Full mailbox access including permanent irreversible message/thread deletion (`messages.delete`). "
        "Not requested by default; move_to_trash only requires gmail.modify."
    ),
}

TOOL_SCOPE_REQUIREMENTS: dict[str, tuple[str, str]] = {
    # Account & diagnostics
    "gmail_get_profile": (SCOPE_GMAIL_MODIFY, "Read authenticated user's Gmail profile and history ID."),
    "gmail_get_mailbox_stats": (SCOPE_GMAIL_MODIFY, "Read label counters and mailbox statistics."),
    # Search & retrieval
    "gmail_search_messages": (SCOPE_GMAIL_MODIFY, "Search and retrieve message headers/bodies."),
    "gmail_search_threads": (SCOPE_GMAIL_MODIFY, "Search conversation threads."),
    "gmail_get_message": (SCOPE_GMAIL_MODIFY, "Retrieve individual email message."),
    "gmail_get_thread": (SCOPE_GMAIL_MODIFY, "Retrieve conversation thread and messages."),
    "gmail_list_unread": (SCOPE_GMAIL_MODIFY, "List and read unread messages."),
    "gmail_get_attachment": (SCOPE_GMAIL_MODIFY, "Download email message attachments."),
    "gmail_export_messages": (SCOPE_GMAIL_MODIFY, "Export messages to JSON/CSV/EML."),
    # Drafts
    "gmail_create_draft": (SCOPE_GMAIL_MODIFY, "Create a new draft in the mailbox."),
    "gmail_get_draft": (SCOPE_GMAIL_MODIFY, "Retrieve an existing draft."),
    "gmail_list_drafts": (SCOPE_GMAIL_MODIFY, "List mailbox drafts."),
    "gmail_duplicate_draft": (SCOPE_GMAIL_MODIFY, "Read raw draft MIME and create duplicate drafts."),
    "gmail_update_draft": (SCOPE_GMAIL_MODIFY, "Update an existing draft."),
    "gmail_delete_draft": (SCOPE_GMAIL_MODIFY, "Delete a draft."),
    "gmail_bulk_create_drafts": (SCOPE_GMAIL_MODIFY, "Bulk-create personalized drafts."),
    "gmail_bulk_attach_to_drafts": (SCOPE_GMAIL_MODIFY, "Read and update multiple drafts with attachments."),
    # Sending & replies
    "gmail_send_email": (SCOPE_GMAIL_MODIFY, "Send an outgoing email message."),
    "gmail_send_reply": (SCOPE_GMAIL_MODIFY, "Read thread metadata and send a threaded reply."),
    "gmail_send_reply_all": (SCOPE_GMAIL_MODIFY, "Read thread metadata and reply to all recipients."),
    "gmail_forward_message": (SCOPE_GMAIL_MODIFY, "Read original message and forward to recipients."),
    "gmail_prepare_bulk_send": (SCOPE_GMAIL_MODIFY, "Check sent mail headroom and prepare bulk send plan."),
    "gmail_execute_bulk_send": (SCOPE_GMAIL_MODIFY, "Execute approved bulk email send plan."),
    "gmail_prepare_bulk_reply": (SCOPE_GMAIL_MODIFY, "Fetch source thread headers and prepare bulk reply plan."),
    "gmail_execute_bulk_reply": (SCOPE_GMAIL_MODIFY, "Execute approved bulk thread replies."),
    # Attachments
    "gmail_list_attachments": (SCOPE_GMAIL_MODIFY, "Inspect message MIME structure for attachments."),
    "gmail_attach_files_to_message": (SCOPE_GMAIL_MODIFY, "Attach files to a draft or MIME message."),
    # Labels & mailbox organization
    "gmail_list_labels": (SCOPE_GMAIL_MODIFY, "List system and user labels."),
    "gmail_create_label": (SCOPE_GMAIL_MODIFY, "Create a custom user label."),
    "gmail_update_label": (SCOPE_GMAIL_MODIFY, "Update custom label properties."),
    "gmail_delete_label": (SCOPE_GMAIL_MODIFY, "Delete a custom user label."),
    "gmail_apply_label": (SCOPE_GMAIL_MODIFY, "Apply labels to messages or threads."),
    "gmail_remove_label": (SCOPE_GMAIL_MODIFY, "Remove labels from messages or threads."),
    "gmail_bulk_label_messages": (SCOPE_GMAIL_MODIFY, "Batch-apply labels to many messages."),
    "gmail_modify_messages": (SCOPE_GMAIL_MODIFY, "Modify message/thread read, archive, star, spam, or trash state."),
    # Filters & settings
    "gmail_list_filters": (SCOPE_GMAIL_SETTINGS_BASIC, "List Gmail message filters."),
    "gmail_get_filter": (SCOPE_GMAIL_SETTINGS_BASIC, "Retrieve a specific Gmail filter."),
    "gmail_create_filter": (SCOPE_GMAIL_SETTINGS_BASIC, "Create a new Gmail filter."),
    "gmail_delete_filter": (SCOPE_GMAIL_SETTINGS_BASIC, "Delete a Gmail filter."),
    "gmail_get_mailbox_settings": (SCOPE_GMAIL_SETTINGS_BASIC, "Read vacation responder, send-as aliases, and forwarding settings."),
    "gmail_update_vacation_settings": (SCOPE_GMAIL_SETTINGS_BASIC, "Update vacation responder settings."),
    # Scheduling & synchronization
    "gmail_schedule_send": (SCOPE_GMAIL_MODIFY, "Schedule an outgoing email for future delivery."),
    "gmail_process_due_scheduled_sends": (SCOPE_GMAIL_MODIFY, "Send due scheduled emails via Gmail API."),
    "gmail_start_watch": (SCOPE_GMAIL_MODIFY, "Start push notification watch on the mailbox."),
    "gmail_sync_history": (SCOPE_GMAIL_MODIFY, "Synchronize incremental mailbox changes via history.list."),
    "gmail_stop_watch": (SCOPE_GMAIL_MODIFY, "Stop active push notification watch."),
}


def build_insufficient_scope_report(
    tool_name: str,
    granted_scopes: list[str] | set[str],
    required_scope_override: str | None = None,
) -> dict[str, Any]:
    """Construct a detailed diagnostic report when a required OAuth scope is missing."""
    req_scope, reason = TOOL_SCOPE_REQUIREMENTS.get(
        tool_name,
        (SCOPE_GMAIL_MODIFY, f"Execute tool '{tool_name}' against the Gmail REST API v1."),
    )
    if required_scope_override:
        req_scope = required_scope_override
        reason = SCOPE_DESCRIPTIONS.get(req_scope, reason)

    granted_set = set(granted_scopes or [])
    missing_scopes = [s for s in DEFAULT_SCOPES if s not in granted_set]
    if req_scope not in granted_set and req_scope not in missing_scopes:
        missing_scopes.insert(0, req_scope)

    return {
        "error_code": "INSUFFICIENT_OAUTH_SCOPE",
        "missing_scope": req_scope,
        "missing_scopes_all": missing_scopes,
        "granted_scopes": sorted(granted_set),
        "tool_requiring_scope": tool_name,
        "why_scope_is_required": reason,
        "scope_description": SCOPE_DESCRIPTIONS.get(req_scope, ""),
        "reauthorization_steps": [
            "1. Ensure the Gmail API is enabled in Google Cloud Console for project 542762227233.",
            f"2. Ensure the OAuth Consent Screen includes scope: {req_scope}",
            "3. Run: uv run --no-project python -m gmail_mcp.auth.oauth --login --force-reauth",
            "4. Approve all requested permissions in the browser window to update token.json.",
        ],
    }
