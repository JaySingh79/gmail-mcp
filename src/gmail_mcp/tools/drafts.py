"""Single draft management tools (`create`, `get`, `list`, `duplicate`, `update`, `delete`)."""

from __future__ import annotations

from typing import Any, Literal

from gmail_mcp.config import get_quota_cost
from gmail_mcp.core.gmail_client import GmailClientWrapper
from gmail_mcp.core.pagination import collect_paginated_items
from gmail_mcp.core.safety import SafetyError


def resolve_content_or_template(
    client: GmailClientWrapper,
    *,
    subject: str | None,
    body_text: str | None,
    body_html: str | None,
    template_id: str | None,
    variables: dict[str, Any] | None,
    attachments: list[str] | None,
) -> dict[str, Any]:
    """Resolve message subject, bodies, and attachments from direct parameters or a stored SQLite template."""
    eff_attachments = list(attachments or [])
    if template_id:
        tpl = client.template_repo.get(template_id)
        if not tpl:
            raise SafetyError("TEMPLATE_NOT_FOUND", f"Template '{template_id}' not found.")
        rendered = client.safety.render_template_content(
            subject=subject if subject is not None else tpl.subject,
            body_text=body_text if body_text is not None else tpl.body_text,
            body_html=body_html if body_html is not None else tpl.body_html,
            variables=variables,
            defaults=tpl.defaults,
            required_variables=tpl.variables if (subject is None and body_text is None and body_html is None) else None,
        )
        for tpl_att in tpl.attachments:
            if tpl_att not in eff_attachments:
                eff_attachments.append(tpl_att)
        return {
            "subject": rendered["subject"] or "",
            "body_text": rendered["body_text"],
            "body_html": rendered["body_html"],
            "attachments": eff_attachments,
        }

    if subject is None:
        subject = ""
    if body_text is None and body_html is None:
        raise SafetyError(
            "EMPTY_MESSAGE_BODY",
            "Provide `body_text`, `body_html`, or a valid `template_id`.",
        )
    return {
        "subject": subject,
        "body_text": body_text,
        "body_html": body_html,
        "attachments": eff_attachments,
    }


async def gmail_create_draft(
    client: GmailClientWrapper,
    to: list[str] | str,
    subject: str | None = None,
    *,
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
    """Create a new draft in Gmail (supporting direct content or SQLite template rendering + attachments)."""
    try:
        resolved = resolve_content_or_template(
            client,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            template_id=template_id,
            variables=variables,
            attachments=attachments,
        )
        built = client.mime_builder.build_email_message(
            to=to,
            cc=cc,
            bcc=bcc,
            reply_to=reply_to,
            subject=resolved["subject"],
            body_text=resolved["body_text"],
            body_html=resolved["body_html"],
            in_reply_to=in_reply_to,
            references=references,
            attachments=resolved["attachments"],
        )
        draft_body: dict[str, Any] = {"message": {"raw": built["raw"]}}
        if thread_id:
            draft_body["message"]["threadId"] = thread_id

        resp, meta = await client.execute_api(
            "drafts.create",
            {"body": draft_body},
            tool_name="gmail_create_draft",
            op_class="write",
            attachment_bytes=built["raw_mime_size_bytes"],
        )
        msg_obj = resp.get("message") or {}
        return {
            "success": True,
            "operation": "gmail_create_draft",
            "mutates_mailbox": True,
            "draft_id": resp.get("id"),
            "message_id": msg_obj.get("id"),
            "thread_id": msg_obj.get("threadId") or thread_id,
            "rfc_message_id": built["rfc_message_id"],
            "recipients": built["recipients"]["public_summary"],
            "subject": resolved["subject"],
            "attachments": built["attachments"],
            "warnings": built["warnings"],
            "quota_estimate": {
                "units_per_request": 10,
                "total_units": int(meta.get("quota_estimate", 10)),
            },
        }
    except Exception as exc:
        return client.format_tool_error("gmail_create_draft", exc, mutates_mailbox=True)


async def gmail_get_draft(
    client: GmailClientWrapper,
    draft_id: str,
    *,
    format: Literal["minimal", "metadata", "full", "raw"] = "full",
) -> dict[str, Any]:
    """Retrieve a particular draft by ID in minimal, metadata, full, or raw format."""
    try:
        fmt_lower = format.lower()
        unit_cost = get_quota_cost("drafts.get", fmt=fmt_lower)
        resp, meta = await client.execute_api(
            "drafts.get",
            {"id": draft_id, "format": fmt_lower},
            tool_name="gmail_get_draft",
            op_class="read",
            fmt=fmt_lower,
        )
        msg_res = resp.get("message") or {}
        if fmt_lower == "raw" and msg_res.get("raw"):
            parsed_raw = client.mime_builder.parse_raw_mime(msg_res["raw"])
            draft_data = {
                "draft_id": resp.get("id") or draft_id,
                "message_id": msg_res.get("id"),
                "thread_id": msg_res.get("threadId"),
                "raw": msg_res.get("raw"),
                "headers": parsed_raw["headers"],
                "subject": parsed_raw["subject"],
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
            parsed = client.mime_builder.parse_gmail_payload(msg_res)
            draft_data = {
                "draft_id": resp.get("id") or draft_id,
                "message": parsed,
            }

        return {
            "success": True,
            "operation": "gmail_get_draft",
            "mutates_mailbox": False,
            "format": fmt_lower,
            "draft": draft_data,
            "quota_estimate": {
                "units_per_request": unit_cost,
                "total_units": int(meta.get("quota_estimate", unit_cost)),
            },
        }
    except Exception as exc:
        return client.format_tool_error("gmail_get_draft", exc, mutates_mailbox=False)


async def gmail_list_drafts(
    client: GmailClientWrapper,
    *,
    query: str | None = None,
    max_results: int = 50,
    page_token: str | None = None,
    include_spam_trash: bool = False,
    auto_paginate: bool = False,
) -> dict[str, Any]:
    """List existing drafts with pagination support."""
    try:
        total_units = 0

        async def _fetch_page(next_tok: str | None, page_sz: int) -> dict[str, Any]:
            nonlocal total_units
            params: dict[str, Any] = {
                "maxResults": page_sz,
                "includeSpamTrash": include_spam_trash,
            }
            if query and query.strip():
                params["q"] = query.strip()
            if next_tok:
                params["pageToken"] = next_tok
            resp, meta = await client.execute_api(
                "drafts.list",
                params,
                tool_name="gmail_list_drafts",
                op_class="read",
            )
            total_units += int(meta.get("quota_estimate", 5))
            return resp

        page_data = await collect_paginated_items(
            _fetch_page,
            items_key="drafts",
            page_size=min(500, max(1, max_results)),
            max_results=max_results,
            start_page_token=page_token,
            auto_paginate_all=auto_paginate,
        )
        return {
            "success": True,
            "operation": "gmail_list_drafts",
            "mutates_mailbox": False,
            "results": page_data["results"],
            "returned_count": page_data["returned_count"],
            "estimated_total": page_data["estimated_total"],
            "has_more": page_data["has_more"],
            "next_page_token": page_data["next_page_token"],
            "quota_estimate": {
                "units_per_request": 5,
                "total_units": total_units,
            },
        }
    except Exception as exc:
        return client.format_tool_error("gmail_list_drafts", exc, mutates_mailbox=False)


async def gmail_duplicate_draft(
    client: GmailClientWrapper,
    source_draft_id: str,
    *,
    copies: int = 1,
    recipient_overrides: list[str] | None = None,
    subject_override: str | None = None,
    preserve_thread: bool = False,
) -> dict[str, Any]:
    """
    Duplicate an existing draft `1..N` times through raw MIME reconstruction:
    1. `drafts.get` with `format=raw`
    2. Decode base64url MIME content
    3. Parse MIME message
    4. Reconstruct a new MIME message for each copy (generating a fresh Message-ID)
    5. Apply recipient/subject overrides while preserving body, formatting, and attachments
    6. Create independent new drafts (unless `preserve_thread=True` is explicitly requested).
    """
    try:
        num_copies = max(len(recipient_overrides or []), int(copies))
        if num_copies < 1:
            raise SafetyError("INVALID_COPY_COUNT", "`copies` must be at least 1.")

        get_resp, g_meta = await client.execute_api(
            "drafts.get",
            {"id": source_draft_id, "format": "raw"},
            tool_name="gmail_duplicate_draft",
            op_class="read",
            fmt="raw",
        )
        total_units = int(g_meta.get("quota_estimate", 60))
        msg_obj = get_resp.get("message") or {}
        source_raw_b64 = msg_obj.get("raw") or ""
        if not source_raw_b64:
            raise SafetyError("EMPTY_SOURCE_DRAFT", f"Source draft '{source_draft_id}' did not return raw MIME data.")

        source_thread_id = msg_obj.get("threadId") if preserve_thread else None

        created_drafts: list[dict[str, Any]] = []
        failed_drafts: list[dict[str, Any]] = []

        for idx in range(num_copies):
            override_recip = (
                recipient_overrides[idx]
                if recipient_overrides and idx < len(recipient_overrides)
                else None
            )
            try:
                reconstructed = client.mime_builder.reconstruct_mime_from_raw(
                    source_raw_b64,
                    override_to=override_recip,
                    override_subject=subject_override,
                    preserve_existing_attachments=True,
                    preserve_threading_headers=preserve_thread,
                )
                body_payload: dict[str, Any] = {"message": {"raw": reconstructed["raw"]}}
                if preserve_thread and source_thread_id:
                    body_payload["message"]["threadId"] = source_thread_id

                c_resp, c_meta = await client.execute_api(
                    "drafts.create",
                    {"body": body_payload},
                    tool_name="gmail_duplicate_draft",
                    op_class="write",
                    attachment_bytes=reconstructed["raw_mime_size_bytes"],
                )
                total_units += int(c_meta.get("quota_estimate", 10))
                new_id = c_resp.get("id")
                if new_id == source_draft_id:
                    raise SafetyError("DUPLICATE_ID_COLLISION", "Duplicate draft unexpectedly reused source draft ID.")
                created_drafts.append(
                    {
                        "copy_index": idx + 1,
                        "draft_id": new_id,
                        "message_id": (c_resp.get("message") or {}).get("id"),
                        "thread_id": (c_resp.get("message") or {}).get("threadId") if preserve_thread else None,
                        "recipient": (
                            override_recip
                            or ", ".join(reconstructed["recipients"].get("to") or [])
                        ),
                        "attachments_preserved": len(reconstructed["attachments"]),
                        "status": "created",
                    }
                )
            except Exception as item_exc:
                err_info = client.format_tool_error("gmail_duplicate_draft", item_exc, mutates_mailbox=True)
                failed_drafts.append(
                    {
                        "copy_index": idx + 1,
                        "recipient": override_recip,
                        "status": "failed",
                        "error_code": err_info["error_code"],
                        "message": err_info["message"],
                        "retryable": err_info["retryable"],
                    }
                )

        all_succeeded = len(failed_drafts) == 0
        return {
            "success": all_succeeded,
            "operation": "gmail_duplicate_draft",
            "mutates_mailbox": True,
            "source_draft_id": source_draft_id,
            "preserve_thread": preserve_thread,
            "requested": num_copies,
            "created": len(created_drafts),
            "failed": len(failed_drafts),
            "skipped": 0,
            "drafts": created_drafts,
            "failures": failed_drafts,
            "retryable_failures": [f for f in failed_drafts if f.get("retryable")],
            "non_retryable_failures": [f for f in failed_drafts if not f.get("retryable")],
            "safe_to_continue": True,
            "quota_estimate": {
                "units_per_request": 10,
                "total_units": total_units,
            },
        }
    except Exception as exc:
        return client.format_tool_error("gmail_duplicate_draft", exc, mutates_mailbox=True)


async def gmail_update_draft(
    client: GmailClientWrapper,
    draft_id: str,
    *,
    to: list[str] | str | None = None,
    cc: list[str] | str | None = None,
    bcc: list[str] | str | None = None,
    subject: str | None = None,
    body_text: str | None = None,
    body_html: str | None = None,
    additional_attachments: list[str] | None = None,
    preserve_existing_attachments: bool = True,
) -> dict[str, Any]:
    """
    Modify an existing draft while preserving existing attachments, recipients, subject, and body
    unless explicitly overridden.
    """
    try:
        get_resp, g_meta = await client.execute_api(
            "drafts.get",
            {"id": draft_id, "format": "raw"},
            tool_name="gmail_update_draft",
            op_class="read",
            fmt="raw",
        )
        total_units = int(g_meta.get("quota_estimate", 60))
        msg_obj = get_resp.get("message") or {}
        source_raw_b64 = msg_obj.get("raw") or ""
        thread_id = msg_obj.get("threadId")

        reconstructed = client.mime_builder.reconstruct_mime_from_raw(
            source_raw_b64,
            override_to=to,
            override_cc=cc,
            override_bcc=bcc,
            override_subject=subject,
            override_body_text=body_text,
            override_body_html=body_html,
            additional_attachments=additional_attachments,
            preserve_existing_attachments=preserve_existing_attachments,
            preserve_threading_headers=bool(thread_id),
        )

        upd_body: dict[str, Any] = {"message": {"raw": reconstructed["raw"]}}
        if thread_id:
            upd_body["message"]["threadId"] = thread_id

        upd_resp, u_meta = await client.execute_api(
            "drafts.update",
            {"id": draft_id, "body": upd_body},
            tool_name="gmail_update_draft",
            op_class="write",
            attachment_bytes=reconstructed["raw_mime_size_bytes"],
        )
        total_units += int(u_meta.get("quota_estimate", 15))
        return {
            "success": True,
            "operation": "gmail_update_draft",
            "mutates_mailbox": True,
            "draft_id": upd_resp.get("id") or draft_id,
            "message_id": (upd_resp.get("message") or {}).get("id"),
            "thread_id": (upd_resp.get("message") or {}).get("threadId") or thread_id,
            "attachments": reconstructed["attachments"],
            "warnings": reconstructed["warnings"],
            "quota_estimate": {
                "units_per_request": 75,
                "total_units": total_units,
            },
        }
    except Exception as exc:
        return client.format_tool_error("gmail_update_draft", exc, mutates_mailbox=True)


async def gmail_delete_draft(
    client: GmailClientWrapper,
    draft_id: str,
) -> dict[str, Any]:
    """Delete a draft from the mailbox."""
    try:
        _, meta = await client.execute_api(
            "drafts.delete",
            {"id": draft_id},
            tool_name="gmail_delete_draft",
            op_class="write",
        )
        return {
            "success": True,
            "operation": "gmail_delete_draft",
            "mutates_mailbox": True,
            "draft_id": draft_id,
            "deleted": True,
            "quota_estimate": {
                "units_per_request": 10,
                "total_units": int(meta.get("quota_estimate", 10)),
            },
        }
    except Exception as exc:
        return client.format_tool_error("gmail_delete_draft", exc, mutates_mailbox=True)
