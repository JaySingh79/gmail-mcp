"""Low-latency server-side Gmail search, unread listing, and message export tools."""

from __future__ import annotations

import asyncio
import csv
import json
from pathlib import Path
from typing import Any, Callable, Literal

from gmail_mcp.config import get_quota_cost
from gmail_mcp.core.gmail_client import GmailClientWrapper
from gmail_mcp.core.pagination import build_gmail_search_query, collect_paginated_items
from gmail_mcp.core.safety import SafetyError

DEFAULT_METADATA_HEADERS = [
    "From",
    "To",
    "Cc",
    "Subject",
    "Date",
    "Message-ID",
    "In-Reply-To",
    "References",
    "Reply-To",
]


async def gmail_search_messages(
    client: GmailClientWrapper,
    query: str | None = None,
    *,
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
    """
    Search emails server-side using Gmail query syntax and retrieve matching messages with bounded concurrency.
    Never modifies the UNREAD label. Defaults to `format='metadata'` for low latency.
    """
    try:
        effective_query = build_gmail_search_query(
            query,
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

        total_units = 0

        async def _fetch_page(next_tok: str | None, page_sz: int) -> dict[str, Any]:
            nonlocal total_units
            params: dict[str, Any] = {
                "maxResults": page_sz,
                "includeSpamTrash": include_spam_trash,
            }
            if effective_query:
                params["q"] = effective_query
            if next_tok:
                params["pageToken"] = next_tok
            resp, meta = await client.execute_api(
                "messages.list",
                params,
                tool_name="gmail_search_messages",
                op_class="read",
            )
            total_units += int(meta.get("quota_estimate", 5))
            return resp

        page_data = await collect_paginated_items(
            _fetch_page,
            items_key="messages",
            page_size=min(500, max(1, max_results)),
            max_results=max_results,
            start_page_token=page_token,
            auto_paginate_all=auto_paginate,
        )

        stubs = page_data["results"]
        if not fetch_details or not stubs:
            return {
                "success": True,
                "operation": "gmail_search_messages",
                "mutates_mailbox": False,
                "query": effective_query,
                "format": "id_only" if not fetch_details else format,
                "results": stubs,
                "returned_count": len(stubs),
                "estimated_total": page_data["estimated_total"],
                "has_more": page_data["has_more"],
                "next_page_token": page_data["next_page_token"],
                "quota_estimate": {
                    "units_per_request": 5,
                    "total_units": total_units,
                },
            }

        fmt_lower = format.lower()
        get_unit_cost = get_quota_cost("messages.get", fmt=fmt_lower)

        async def _fetch_single(stub: dict[str, Any]) -> dict[str, Any]:
            nonlocal total_units
            mid = stub["id"]
            params: dict[str, Any] = {"id": mid, "format": fmt_lower}
            if fmt_lower == "metadata":
                params["metadataHeaders"] = DEFAULT_METADATA_HEADERS
            msg_resp, meta = await client.execute_api(
                "messages.get",
                params,
                tool_name="gmail_search_messages",
                op_class="read",
                fmt=fmt_lower,
            )
            total_units += int(meta.get("quota_estimate", get_unit_cost))
            if fmt_lower == "raw":
                parsed_raw = client.mime_builder.parse_raw_mime(msg_resp.get("raw", ""))
                return {
                    "id": msg_resp.get("id"),
                    "thread_id": msg_resp.get("threadId"),
                    "label_ids": msg_resp.get("labelIds") or [],
                    "snippet": msg_resp.get("snippet"),
                    "raw": msg_resp.get("raw"),
                    "subject": parsed_raw["subject"],
                    "from": parsed_raw["from"],
                    "to": parsed_raw["to"],
                }
            parsed = client.mime_builder.parse_gmail_payload(msg_resp)
            if fmt_lower in ("metadata", "minimal"):
                parsed.pop("body_text", None)
                parsed.pop("body_html", None)
            return parsed

        hydrated = await asyncio.gather(*[_fetch_single(s) for s in stubs])
        return {
            "success": True,
            "operation": "gmail_search_messages",
            "mutates_mailbox": False,
            "query": effective_query,
            "format": fmt_lower,
            "results": list(hydrated),
            "returned_count": len(hydrated),
            "estimated_total": page_data["estimated_total"],
            "has_more": page_data["has_more"],
            "next_page_token": page_data["next_page_token"],
            "quota_estimate": {
                "units_per_request": get_unit_cost,
                "total_units": total_units,
            },
        }
    except Exception as exc:
        return client.format_tool_error("gmail_search_messages", exc, mutates_mailbox=False)


async def gmail_search_threads(
    client: GmailClientWrapper,
    query: str = "",
    *,
    max_results: int = 20,
    page_token: str | None = None,
    auto_paginate: bool = False,
    include_spam_trash: bool = False,
) -> dict[str, Any]:
    """Search conversation threads using Gmail query syntax and return thread identifiers and snippets."""
    try:
        total_units = 0

        async def _fetch_page(next_tok: str | None, page_sz: int) -> dict[str, Any]:
            nonlocal total_units
            params: dict[str, Any] = {
                "maxResults": page_sz,
                "includeSpamTrash": include_spam_trash,
            }
            if query.strip():
                params["q"] = query.strip()
            if next_tok:
                params["pageToken"] = next_tok
            resp, meta = await client.execute_api(
                "threads.list",
                params,
                tool_name="gmail_search_threads",
                op_class="read",
            )
            total_units += int(meta.get("quota_estimate", 10))
            return resp

        page_data = await collect_paginated_items(
            _fetch_page,
            items_key="threads",
            page_size=min(500, max(1, max_results)),
            max_results=max_results,
            start_page_token=page_token,
            auto_paginate_all=auto_paginate,
        )
        return {
            "success": True,
            "operation": "gmail_search_threads",
            "mutates_mailbox": False,
            "query": query.strip(),
            "results": page_data["results"],
            "returned_count": page_data["returned_count"],
            "estimated_total": page_data["estimated_total"],
            "has_more": page_data["has_more"],
            "next_page_token": page_data["next_page_token"],
            "quota_estimate": {
                "units_per_request": 10,
                "total_units": total_units,
            },
        }
    except Exception as exc:
        return client.format_tool_error("gmail_search_threads", exc, mutates_mailbox=False)


async def gmail_list_unread(
    client: GmailClientWrapper,
    *,
    query: str | None = None,
    max_results_per_page: int = 500,
    max_total_results: int | None = None,
    page_token: str | None = None,
    include_body: bool = False,
    mark_as_read: bool = False,
) -> dict[str, Any]:
    """
    Retrieve unread emails across pages without losing results.
    Requirement: Never mark unread messages as read just because they were retrieved.
    If `mark_as_read=True` is passed, it is rejected unless `gmail_modify_messages` is explicitly used.
    """
    try:
        if mark_as_read:
            raise SafetyError(
                "MARK_READ_ON_RETRIEVAL_FORBIDDEN",
                "Retrieving unread emails must never mark them as read. Use `gmail_modify_messages` explicitly.",
            )

        unread_query = f"is:unread {query.strip()}".strip() if query and query.strip() else "is:unread"
        total_units = 0

        async def _fetch_page(next_tok: str | None, page_sz: int) -> dict[str, Any]:
            nonlocal total_units
            params: dict[str, Any] = {
                "q": unread_query,
                "maxResults": page_sz,
            }
            if next_tok:
                params["pageToken"] = next_tok
            resp, meta = await client.execute_api(
                "messages.list",
                params,
                tool_name="gmail_list_unread",
                op_class="read",
            )
            total_units += int(meta.get("quota_estimate", 5))
            return resp

        page_data = await collect_paginated_items(
            _fetch_page,
            items_key="messages",
            page_size=min(500, max(1, max_results_per_page)),
            max_results=max_total_results,
            start_page_token=page_token,
            auto_paginate_all=True,
        )

        stubs = page_data["results"]
        target_fmt: Literal["metadata", "full"] = "full" if include_body else "metadata"
        unit_cost = get_quota_cost("messages.get", fmt=target_fmt)

        async def _hydrate(stub: dict[str, Any]) -> dict[str, Any]:
            nonlocal total_units
            params: dict[str, Any] = {"id": stub["id"], "format": target_fmt}
            if target_fmt == "metadata":
                params["metadataHeaders"] = DEFAULT_METADATA_HEADERS
            msg_resp, meta = await client.execute_api(
                "messages.get",
                params,
                tool_name="gmail_list_unread",
                op_class="read",
                fmt=target_fmt,
            )
            total_units += int(meta.get("quota_estimate", unit_cost))
            parsed = client.mime_builder.parse_gmail_payload(msg_resp)
            if not include_body:
                parsed.pop("body_text", None)
                parsed.pop("body_html", None)
            return parsed

        messages = await asyncio.gather(*[_hydrate(s) for s in stubs])
        return {
            "success": True,
            "operation": "gmail_list_unread",
            "mutates_mailbox": False,
            "unread_preserved": True,
            "query": unread_query,
            "format": target_fmt,
            "results": list(messages),
            "returned_count": len(messages),
            "estimated_total": page_data["estimated_total"],
            "has_more": page_data["has_more"],
            "next_page_token": page_data["next_page_token"],
            "pages_fetched": page_data["pages_fetched"],
            "quota_estimate": {
                "units_per_request": unit_cost,
                "total_units": total_units,
            },
        }
    except Exception as exc:
        return client.format_tool_error("gmail_list_unread", exc, mutates_mailbox=False)


async def gmail_export_messages(
    client: GmailClientWrapper,
    query: str,
    export_format: Literal["json", "csv", "eml"] = "json",
    destination_directory: str = "exports",
    filename_prefix: str = "gmail_export",
    max_results: int = 50,
    include_attachments: bool = False,
    progress_callback: Callable[[int, int], Any] | None = None,
) -> dict[str, Any]:
    """Export matching emails into JSON, CSV metadata, or EML files (plus optional attachments) inside an allowed directory."""
    try:
        dest_dir = client.safety.resolve_safe_path(
            destination_directory, must_exist=False, allow_create_in_allowed_dir=True
        )
        dest_dir.mkdir(parents=True, exist_ok=True)

        fmt_lower = export_format.lower()
        if fmt_lower not in ("json", "csv", "eml"):
            raise SafetyError("INVALID_EXPORT_FORMAT", f"Unsupported export format: '{export_format}'")

        msg_fetch_fmt: Literal["metadata", "full", "raw"] = (
            "raw" if fmt_lower == "eml" else ("full" if fmt_lower == "json" or include_attachments else "metadata")
        )

        search_res = await gmail_search_messages(
            client,
            query=query,
            max_results=max_results,
            format=msg_fetch_fmt,
            fetch_details=True,
            auto_paginate=True,
        )
        if not search_res.get("success"):
            return search_res

        items: list[dict[str, Any]] = search_res.get("results") or []
        total_units = int(search_res.get("quota_estimate", {}).get("total_units", 0))
        exported_files: list[str] = []
        saved_attachments: list[str] = []

        if fmt_lower == "json":
            out_file = dest_dir / f"{filename_prefix}.json"
            out_file.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
            exported_files.append(str(out_file))

        elif fmt_lower == "csv":
            out_file = dest_dir / f"{filename_prefix}.csv"
            with out_file.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(
                    fh,
                    fieldnames=["id", "thread_id", "date", "from", "to", "cc", "subject", "rfc_message_id", "snippet"],
                )
                writer.writeheader()
                for item in items:
                    writer.writerow(
                        {
                            "id": item.get("id", ""),
                            "thread_id": item.get("thread_id", ""),
                            "date": item.get("date", ""),
                            "from": item.get("from", ""),
                            "to": item.get("to", ""),
                            "cc": item.get("cc", ""),
                            "subject": item.get("subject", ""),
                            "rfc_message_id": item.get("rfc_message_id", ""),
                            "snippet": item.get("snippet", ""),
                        }
                    )
            exported_files.append(str(out_file))

        elif fmt_lower == "eml":
            eml_dir = dest_dir / f"{filename_prefix}_eml"
            eml_dir.mkdir(parents=True, exist_ok=True)
            for idx, item in enumerate(items, start=1):
                raw_b64 = item.get("raw") or ""
                raw_bytes = client.mime_builder.decode_base64url(raw_b64) if raw_b64 else b""
                mid = item.get("id") or f"msg_{idx}"
                eml_path = eml_dir / f"{mid}.eml"
                eml_path.write_bytes(raw_bytes)
                exported_files.append(str(eml_path))
                if progress_callback:
                    res = progress_callback(idx, len(items))
                    if asyncio.iscoroutine(res):
                        await res

        if include_attachments and fmt_lower != "eml":
            att_dir = dest_dir / f"{filename_prefix}_attachments"
            att_dir.mkdir(parents=True, exist_ok=True)
            for idx, item in enumerate(items, start=1):
                mid = item.get("id")
                for att in item.get("attachments") or []:
                    att_id = att.get("attachment_id")
                    fname = Path(att.get("filename") or "attachment.bin").name
                    if mid and att_id:
                        att_resp, meta = await client.execute_api(
                            "messages.attachments.get",
                            {"messageId": mid, "id": att_id},
                            tool_name="gmail_export_messages",
                            op_class="read",
                        )
                        total_units += int(meta.get("quota_estimate", 20))
                        att_bytes = client.mime_builder.decode_base64url(att_resp.get("data", ""))
                        safe_target = att_dir / f"{mid}_{fname}"
                        safe_target.write_bytes(att_bytes)
                        saved_attachments.append(str(safe_target))
                if progress_callback:
                    res = progress_callback(idx, len(items))
                    if asyncio.iscoroutine(res):
                        await res

        return {
            "success": True,
            "operation": "gmail_export_messages",
            "mutates_mailbox": False,
            "export_format": fmt_lower,
            "exported_count": len(items),
            "exported_files": exported_files,
            "saved_attachments": saved_attachments,
            "destination_directory": str(dest_dir),
            "quota_estimate": {
                "units_per_request": get_quota_cost("messages.get", fmt=msg_fetch_fmt),
                "total_units": total_units,
            },
        }
    except Exception as exc:
        return client.format_tool_error("gmail_export_messages", exc, mutates_mailbox=False)
