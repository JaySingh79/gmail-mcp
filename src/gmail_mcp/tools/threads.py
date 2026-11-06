"""Conversation thread retrieval tool (`gmail_get_thread`)."""

from __future__ import annotations

from typing import Any, Literal

from gmail_mcp.config import get_quota_cost
from gmail_mcp.core.gmail_client import GmailClientWrapper
from gmail_mcp.tools.search import DEFAULT_METADATA_HEADERS


async def gmail_get_thread(
    client: GmailClientWrapper,
    thread_id: str,
    *,
    format: Literal["minimal", "metadata", "full"] = "full",
) -> dict[str, Any]:
    """Retrieve an entire conversation thread and all of its messages."""
    try:
        if not thread_id or not thread_id.strip():
            return {
                "success": False,
                "operation": "gmail_get_thread",
                "mutates_mailbox": False,
                "error_code": "INVALID_ARGUMENT",
                "message": "`thread_id` cannot be empty.",
                "http_status": 400,
                "retryable": False,
            }

        fmt_lower = format.lower()
        unit_cost = get_quota_cost("threads.get", fmt=fmt_lower)
        params: dict[str, Any] = {"id": thread_id.strip(), "format": fmt_lower}
        if fmt_lower == "metadata":
            params["metadataHeaders"] = DEFAULT_METADATA_HEADERS

        resp, meta = await client.execute_api(
            "threads.get",
            params,
            tool_name="gmail_get_thread",
            op_class="read",
            fmt=fmt_lower,
        )
        raw_messages = resp.get("messages") or []
        parsed_messages = []
        for m in raw_messages:
            pm = client.mime_builder.parse_gmail_payload(m)
            if fmt_lower in ("metadata", "minimal"):
                pm.pop("body_text", None)
                pm.pop("body_html", None)
            parsed_messages.append(pm)

        return {
            "success": True,
            "operation": "gmail_get_thread",
            "mutates_mailbox": False,
            "thread_id": resp.get("id") or thread_id,
            "history_id": resp.get("historyId"),
            "format": fmt_lower,
            "message_count": len(parsed_messages),
            "messages": parsed_messages,
            "quota_estimate": {
                "units_per_request": unit_cost,
                "total_units": int(meta.get("quota_estimate", unit_cost)),
            },
        }
    except Exception as exc:
        return client.format_tool_error("gmail_get_thread", exc, mutates_mailbox=False)
