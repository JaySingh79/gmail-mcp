"""Individual email message retrieval tool (`gmail_get_message`)."""

from __future__ import annotations

from typing import Any, Literal

from gmail_mcp.config import get_quota_cost
from gmail_mcp.core.gmail_client import GmailClientWrapper
from gmail_mcp.tools.search import DEFAULT_METADATA_HEADERS


async def gmail_get_message(
    client: GmailClientWrapper,
    message_id: str | None = None,
    *,
    rfc822msgid: str | None = None,
    format: Literal["minimal", "metadata", "full", "raw"] = "full",
) -> dict[str, Any]:
    """
    Retrieve a particular email by Gmail message ID or RFC Message-ID in minimal, metadata, full, or raw format.
    Never modifies the UNREAD label.
    """
    try:
        total_units = 0
        target_id = message_id

        if not target_id and rfc822msgid:
            clean_rfc = rfc822msgid.strip().strip("<>")
            list_resp, l_meta = await client.execute_api(
                "messages.list",
                {"q": f"rfc822msgid:{clean_rfc}", "maxResults": 1},
                tool_name="gmail_get_message",
                op_class="read",
            )
            total_units += int(l_meta.get("quota_estimate", 5))
            msgs = list_resp.get("messages") or []
            if not msgs:
                return {
                    "success": False,
                    "operation": "gmail_get_message",
                    "mutates_mailbox": False,
                    "error_code": "RESOURCE_NOT_FOUND",
                    "message": f"No message found matching RFC Message-ID '{rfc822msgid}'.",
                    "http_status": 404,
                    "retryable": False,
                }
            target_id = msgs[0]["id"]

        if not target_id:
            return {
                "success": False,
                "operation": "gmail_get_message",
                "mutates_mailbox": False,
                "error_code": "INVALID_ARGUMENT",
                "message": "Either `message_id` or `rfc822msgid` must be provided.",
                "http_status": 400,
                "retryable": False,
            }

        fmt_lower = format.lower()
        unit_cost = get_quota_cost("messages.get", fmt=fmt_lower)
        params: dict[str, Any] = {"id": target_id, "format": fmt_lower}
        if fmt_lower == "metadata":
            params["metadataHeaders"] = DEFAULT_METADATA_HEADERS

        resp, meta = await client.execute_api(
            "messages.get",
            params,
            tool_name="gmail_get_message",
            op_class="read",
            fmt=fmt_lower,
        )
        total_units += int(meta.get("quota_estimate", unit_cost))

        if fmt_lower == "raw":
            parsed_raw = client.mime_builder.parse_raw_mime(resp.get("raw", ""))
            message_data = {
                "id": resp.get("id"),
                "thread_id": resp.get("threadId"),
                "label_ids": resp.get("labelIds") or [],
                "snippet": resp.get("snippet"),
                "raw": resp.get("raw"),
                "headers": parsed_raw["headers"],
                "subject": parsed_raw["subject"],
                "from": parsed_raw["from"],
                "to": parsed_raw["to"],
                "cc": parsed_raw["cc"],
                "body_text": parsed_raw["body_text"],
                "body_html": parsed_raw["body_html"],
                "attachments": [
                    {
                        "filename": a["filename"],
                        "mime_type": a["mime_type"],
                        "size_bytes": a["size_bytes"],
                    }
                    for a in parsed_raw["attachments"]
                ],
            }
        else:
            message_data = client.mime_builder.parse_gmail_payload(resp)
            if fmt_lower in ("metadata", "minimal"):
                message_data.pop("body_text", None)
                message_data.pop("body_html", None)

        return {
            "success": True,
            "operation": "gmail_get_message",
            "mutates_mailbox": False,
            "format": fmt_lower,
            "message": message_data,
            "quota_estimate": {
                "units_per_request": unit_cost,
                "total_units": total_units,
            },
        }
    except Exception as exc:
        return client.format_tool_error("gmail_get_message", exc, mutates_mailbox=False)
