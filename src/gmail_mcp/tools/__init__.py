"""Exports all 42+ MCP tool functions across the 10 functional domains."""

from gmail_mcp.tools.account import (
    gmail_auth_status,
    gmail_get_mailbox_stats,
    gmail_get_profile,
    gmail_get_runtime_limits,
    gmail_health_check,
)
from gmail_mcp.tools.attachments import (
    gmail_attach_files_to_message,
    gmail_get_attachment,
    gmail_list_attachments,
)
from gmail_mcp.tools.bulk_operations import (
    gmail_bulk_attach_to_drafts,
    gmail_bulk_create_drafts,
)
from gmail_mcp.tools.drafts import (
    gmail_create_draft,
    gmail_delete_draft,
    gmail_duplicate_draft,
    gmail_get_draft,
    gmail_list_drafts,
    gmail_update_draft,
)
from gmail_mcp.tools.filters import (
    gmail_create_filter,
    gmail_delete_filter,
    gmail_get_filter,
    gmail_list_filters,
)
from gmail_mcp.tools.labels import (
    gmail_apply_label,
    gmail_bulk_label_messages,
    gmail_create_label,
    gmail_delete_label,
    gmail_list_labels,
    gmail_remove_label,
    gmail_update_label,
)
from gmail_mcp.tools.mailbox import (
    gmail_get_mailbox_settings,
    gmail_modify_messages,
    gmail_update_vacation_settings,
)
from gmail_mcp.tools.messages import gmail_get_message
from gmail_mcp.tools.scheduling import (
    ScheduledSendWorker,
    gmail_cancel_scheduled_send,
    gmail_list_scheduled_sends,
    gmail_process_due_scheduled_sends,
    gmail_schedule_send,
)
from gmail_mcp.tools.search import (
    gmail_export_messages,
    gmail_list_unread,
    gmail_search_messages,
    gmail_search_threads,
)
from gmail_mcp.tools.sending import (
    gmail_execute_bulk_reply,
    gmail_execute_bulk_send,
    gmail_forward_message,
    gmail_prepare_bulk_reply,
    gmail_prepare_bulk_send,
    gmail_send_email,
    gmail_send_reply,
    gmail_send_reply_all,
)
from gmail_mcp.tools.synchronization import (
    gmail_start_watch,
    gmail_stop_watch,
    gmail_sync_history,
)
from gmail_mcp.tools.templates import (
    gmail_create_template,
    gmail_delete_template,
    gmail_get_template,
    gmail_list_templates,
    gmail_preview_template,
    gmail_update_template,
)
from gmail_mcp.tools.threads import gmail_get_thread

__all__ = [
    "ScheduledSendWorker",
    "gmail_apply_label",
    "gmail_attach_files_to_message",
    "gmail_auth_status",
    "gmail_bulk_attach_to_drafts",
    "gmail_bulk_create_drafts",
    "gmail_bulk_label_messages",
    "gmail_cancel_scheduled_send",
    "gmail_create_draft",
    "gmail_create_filter",
    "gmail_create_label",
    "gmail_create_template",
    "gmail_delete_draft",
    "gmail_delete_filter",
    "gmail_delete_label",
    "gmail_delete_template",
    "gmail_duplicate_draft",
    "gmail_execute_bulk_reply",
    "gmail_execute_bulk_send",
    "gmail_export_messages",
    "gmail_forward_message",
    "gmail_get_attachment",
    "gmail_get_draft",
    "gmail_get_filter",
    "gmail_get_mailbox_settings",
    "gmail_get_mailbox_stats",
    "gmail_get_message",
    "gmail_get_profile",
    "gmail_get_runtime_limits",
    "gmail_get_template",
    "gmail_get_thread",
    "gmail_health_check",
    "gmail_list_attachments",
    "gmail_list_drafts",
    "gmail_list_filters",
    "gmail_list_labels",
    "gmail_list_scheduled_sends",
    "gmail_list_templates",
    "gmail_list_unread",
    "gmail_modify_messages",
    "gmail_prepare_bulk_reply",
    "gmail_prepare_bulk_send",
    "gmail_preview_template",
    "gmail_process_due_scheduled_sends",
    "gmail_remove_label",
    "gmail_schedule_send",
    "gmail_search_messages",
    "gmail_search_threads",
    "gmail_send_email",
    "gmail_send_reply",
    "gmail_send_reply_all",
    "gmail_start_watch",
    "gmail_stop_watch",
    "gmail_sync_history",
    "gmail_update_draft",
    "gmail_update_label",
    "gmail_update_template",
    "gmail_update_vacation_settings",
]
