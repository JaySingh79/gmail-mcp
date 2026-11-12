"""Bulk draft generation and bulk draft attachment tools (`gmail_bulk_*`)."""

from __future__ import annotations

import asyncio
from typing import Any

from gmail_mcp.core.gmail_client import GmailClientWrapper
from gmail_mcp.core.safety import SafetyError
from gmail_mcp.tools.drafts import resolve_content_or_template
from gmail_mcp.tools.sending import _load_recipient_records


async def gmail_bulk_create_drafts(
    client: GmailClientWrapper,
    recipients: list[dict[str, Any] | str] | None = None,
    *,
    recipient_source: str | None = None,
    field_mapping: dict[str, str] | None = None,
    attachment_pattern: str | None = None,
    template_id: str | None = None,
    subject: str | None = None,
    body_text: str | None = None,
    body_html: str | None = None,
    attachments: list[str] | None = None,
    compact: bool = True,
) -> dict[str, Any]:
    """
    Generate 1 to hundreds of drafts in a single logical operation from templates or recipient records.
    Supports per-recipient subjects, bodies, personalization variables, shared attachments, and individual attachments.
    Also accepts `recipient_source` (path to scraped JSON/CSV), `field_mapping`, and `attachment_pattern`.
    """
    try:
        raw_recipients = _load_recipient_records(
            client=client,
            recipients=recipients,
            recipient_source=recipient_source,
            field_mapping=field_mapping,
            attachment_pattern=attachment_pattern,
        )

        shared_attachments = list(attachments or [])
        total_units = 0
        drafts_out: list[dict[str, Any]] = []
        failures_out: list[dict[str, Any]] = []
        safe_to_continue = True

        for idx, item in enumerate(raw_recipients):
            if isinstance(item, str):
                rec_dict: dict[str, Any] = {"email": item}
            elif isinstance(item, dict):
                rec_dict = dict(item)
            else:
                failures_out.append(
                    {
                        "index": idx,
                        "recipient": str(item),
                        "status": "failed",
                        "error_code": "INVALID_RECIPIENT_RECORD",
                        "message": "Recipient entry must be an email string or dictionary.",
                        "retryable": False,
                    }
                )
                continue

            to_addr = rec_dict.get("email") or rec_dict.get("to")
            cc_addr = rec_dict.get("cc")
            bcc_addr = rec_dict.get("bcc")
            item_tpl = rec_dict.get("template_id") or template_id
            item_subj = rec_dict.get("subject") if rec_dict.get("subject") is not None else subject
            item_text = rec_dict.get("body_text") if rec_dict.get("body_text") is not None else body_text
            item_html = rec_dict.get("body_html") if rec_dict.get("body_html") is not None else body_html
            item_atts = list(dict.fromkeys(shared_attachments + list(rec_dict.get("attachments") or [])))

            # Collect personalization variables from top-level keys + nested `variables`
            reserved_keys = {
                "email",
                "to",
                "cc",
                "bcc",
                "template_id",
                "subject",
                "body_text",
                "body_html",
                "attachments",
                "variables",
                "thread_id",
            }
            vars_map: dict[str, Any] = {
                k: v for k, v in rec_dict.items() if k not in reserved_keys
            }
            if isinstance(rec_dict.get("variables"), dict):
                vars_map.update(rec_dict["variables"])

            try:
                resolved = resolve_content_or_template(
                    client,
                    subject=item_subj,
                    body_text=item_text,
                    body_html=item_html,
                    template_id=item_tpl,
                    variables=vars_map,
                    attachments=item_atts,
                )
                built = client.mime_builder.build_email_message(
                    to=to_addr,
                    cc=cc_addr,
                    bcc=bcc_addr,
                    subject=resolved["subject"],
                    body_text=resolved["body_text"],
                    body_html=resolved["body_html"],
                    attachments=resolved["attachments"],
                )
                body_payload: dict[str, Any] = {"message": {"raw": built["raw"]}}
                if rec_dict.get("thread_id"):
                    body_payload["message"]["threadId"] = rec_dict["thread_id"]

                resp, meta = await client.execute_api(
                    "drafts.create",
                    {"body": body_payload},
                    tool_name="gmail_bulk_create_drafts",
                    op_class="write",
                    attachment_bytes=built["raw_mime_size_bytes"],
                )
                total_units += int(meta.get("quota_estimate", 10))
                drafts_out.append(
                    {
                        "index": idx,
                        "draft_id": resp.get("id"),
                        "message_id": (resp.get("message") or {}).get("id"),
                        "recipient": ", ".join(built["recipients"]["to"]),
                        "subject": resolved["subject"],
                        "attachment_count": len(built["attachments"]),
                        "status": "created",
                    }
                )
            except Exception as item_exc:
                err = client.format_tool_error("gmail_bulk_create_drafts", item_exc, mutates_mailbox=True)
                failures_out.append(
                    {
                        "index": idx,
                        "recipient": str(to_addr or ""),
                        "status": "failed",
                        "error_code": err["error_code"],
                        "message": err["message"],
                        "retryable": err["retryable"],
                    }
                )
                if err["error_code"] in (
                    "QUOTA_EXHAUSTED",
                    "USER_RATE_LIMIT_HEADROOM_EXCEEDED",
                    "PROJECT_RATE_LIMIT_HEADROOM_EXCEEDED",
                    "INSUFFICIENT_SCOPE",
                    "AUTH_REAUTHORIZATION_REQUIRED",
                ):
                    safe_to_continue = False
                    break

        requested_count = len(raw_recipients)
        created_count = len(drafts_out)
        failed_count = len(failures_out)
        skipped_count = max(0, requested_count - created_count - failed_count)
        draft_ids = [d["draft_id"] for d in drafts_out if d.get("draft_id")]

        if compact:
            return {
                "success": (created_count == requested_count and failed_count == 0),
                "operation": "bulk_create_drafts",
                "mutates_mailbox": True,
                "requested": requested_count,
                "successful": created_count,
                "created": created_count,
                "failed": failed_count,
                "skipped": skipped_count,
                "draft_ids": draft_ids,
                "drafts": [{"draft_id": d["draft_id"], "recipient": d.get("recipient", "")} for d in drafts_out],
                "sample_drafts": drafts_out[:3],
                "sample_failures": failures_out[:5],
                "safe_to_continue": safe_to_continue,
                "compact": True,
                "quota_estimate": {
                    "units_per_request": 10,
                    "total_units": total_units,
                },
            }

        return {
            "success": (created_count == requested_count and failed_count == 0),
            "operation": "bulk_create_drafts",
            "mutates_mailbox": True,
            "requested": requested_count,
            "successful": created_count,
            "created": created_count,
            "failed": failed_count,
            "skipped": skipped_count,
            "draft_ids": draft_ids,
            "drafts": drafts_out,
            "individual_results": drafts_out + failures_out,
            "retryable_failures": [f for f in failures_out if f.get("retryable")],
            "non_retryable_failures": [f for f in failures_out if not f.get("retryable")],
            "safe_to_continue": safe_to_continue,
            "compact": False,
            "quota_estimate": {
                "units_per_request": 10,
                "total_units": total_units,
            },
        }
    except Exception as exc:
        return client.format_tool_error("bulk_create_drafts", exc, mutates_mailbox=True)


async def gmail_bulk_attach_to_drafts(
    client: GmailClientWrapper,
    draft_ids: list[str],
    attachments: list[str] | None = None,
    *,
    per_draft_attachments: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """
    Attach shared and/or draft-specific files to multiple existing drafts (e.g. 1 PDF to 100 drafts).
    Implemented via `drafts.get(format='raw')`, MIME reconstruction preserving existing content/headers/attachments,
    and individual `drafts.update` calls.
    """
    try:
        if not draft_ids:
            raise SafetyError("EMPTY_DRAFT_LIST", "`draft_ids` list cannot be empty.")

        shared_atts = list(attachments or [])
        per_draft_map = dict(per_draft_attachments or {})
        if not shared_atts and not per_draft_map:
            raise SafetyError(
                "MISSING_ATTACHMENTS",
                "Provide `attachments` (shared) and/or `per_draft_attachments`.",
            )

        # Pre-validate all shared attachment files once before iterating over 100 drafts
        for att in shared_atts:
            client.safety.validate_attachment_file(att)
        for d_atts in per_draft_map.values():
            for att in d_atts:
                client.safety.validate_attachment_file(att)

        total_units = 0
        updated_drafts: list[dict[str, Any]] = []
        failed_drafts: list[dict[str, Any]] = []
        safe_to_continue = True

        for idx, did in enumerate(draft_ids):
            extra_atts = list(dict.fromkeys(shared_atts + list(per_draft_map.get(did) or [])))
            if not extra_atts:
                continue
            try:
                get_resp, g_meta = await client.execute_api(
                    "drafts.get",
                    {"id": did, "format": "raw"},
                    tool_name="gmail_bulk_attach_to_drafts",
                    op_class="read",
                    fmt="raw",
                )
                total_units += int(g_meta.get("quota_estimate", 60))
                msg_obj = get_resp.get("message") or {}
                raw_b64 = msg_obj.get("raw") or ""
                thread_id = msg_obj.get("threadId")
                if not raw_b64:
                    raise SafetyError("EMPTY_DRAFT_MIME", f"Draft '{did}' returned empty raw MIME.")

                reconstructed = client.mime_builder.reconstruct_mime_from_raw(
                    raw_b64,
                    additional_attachments=extra_atts,
                    preserve_existing_attachments=True,
                    preserve_threading_headers=bool(thread_id),
                )
                upd_body: dict[str, Any] = {"message": {"raw": reconstructed["raw"]}}
                if thread_id:
                    upd_body["message"]["threadId"] = thread_id

                upd_resp, u_meta = await client.execute_api(
                    "drafts.update",
                    {"id": did, "body": upd_body},
                    tool_name="gmail_bulk_attach_to_drafts",
                    op_class="write",
                    attachment_bytes=reconstructed["raw_mime_size_bytes"],
                )
                total_units += int(u_meta.get("quota_estimate", 15))
                updated_drafts.append(
                    {
                        "index": idx,
                        "draft_id": upd_resp.get("id") or did,
                        "status": "updated",
                        "total_attachments": len(reconstructed["attachments"]),
                        "attachments": [a["filename"] for a in reconstructed["attachments"]],
                    }
                )
            except Exception as item_exc:
                err = client.format_tool_error("gmail_bulk_attach_to_drafts", item_exc, mutates_mailbox=True)
                failed_drafts.append(
                    {
                        "index": idx,
                        "draft_id": did,
                        "status": "failed",
                        "error_code": err["error_code"],
                        "message": err["message"],
                        "retryable": err["retryable"],
                    }
                )
                if err["error_code"] in (
                    "QUOTA_EXHAUSTED",
                    "USER_RATE_LIMIT_HEADROOM_EXCEEDED",
                    "PROJECT_RATE_LIMIT_HEADROOM_EXCEEDED",
                ):
                    safe_to_continue = False
                    break

        requested_count = len(draft_ids)
        updated_count = len(updated_drafts)
        failed_count = len(failed_drafts)
        skipped_count = max(0, requested_count - updated_count - failed_count)

        return {
            "success": (updated_count == requested_count and failed_count == 0),
            "operation": "bulk_attach_to_drafts",
            "mutates_mailbox": True,
            "requested": requested_count,
            "successful": updated_count,
            "updated": updated_count,
            "failed": failed_count,
            "skipped": skipped_count,
            "drafts": updated_drafts,
            "individual_results": updated_drafts + failed_drafts,
            "retryable_failures": [f for f in failed_drafts if f.get("retryable")],
            "non_retryable_failures": [f for f in failed_drafts if not f.get("retryable")],
            "safe_to_continue": safe_to_continue,
            "quota_estimate": {
                "units_per_request": 75,
                "total_units": total_units,
            },
        }
    except Exception as exc:
        return client.format_tool_error("bulk_attach_to_drafts", exc, mutates_mailbox=True)
