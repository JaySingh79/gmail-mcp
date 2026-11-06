"""Email sending, threaded replies, forwarding, and two-phase bulk send/reply tools."""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from gmail_mcp.core.gmail_client import GmailClientWrapper
from gmail_mcp.core.quota_manager import QuotaExhaustedError
from gmail_mcp.core.retry_manager import GmailMCPError
from gmail_mcp.core.safety import SafetyError
from gmail_mcp.tools.drafts import resolve_content_or_template
from gmail_mcp.tools.search import DEFAULT_METADATA_HEADERS


def _compute_default_idempotency_key(
    operation: str,
    to: list[str],
    cc: list[str],
    bcc: list[str],
    subject: str,
    body_text: str | None,
    body_html: str | None,
    thread_id: str | None,
    attachments: list[str],
) -> str:
    payload = {
        "op": operation,
        "to": [x.lower().strip() for x in to],
        "cc": [x.lower().strip() for x in cc],
        "bcc": [x.lower().strip() for x in bcc],
        "subject": subject,
        "body_text": body_text or "",
        "body_html": body_html or "",
        "thread_id": thread_id or "",
        "attachments": sorted(attachments),
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:24]
    return f"idem_{operation}_{digest}"


async def _validate_from_alias(client: GmailClientWrapper, from_alias: str | None, tool_name: str) -> str | None:
    """Verify that a requested `from_alias` is an authorized, verified send-as alias in Gmail."""
    if not from_alias:
        return None
    bare_alias = client.safety.extract_email_address(from_alias).lower()
    resp, _ = await client.execute_api(
        "settings.sendAs.list",
        {},
        tool_name=tool_name,
        op_class="read",
    )
    aliases = resp.get("sendAs") or []
    for entry in aliases:
        send_as_email = (entry.get("sendAsEmail") or "").lower()
        status = (entry.get("verificationStatus") or "accepted").lower()
        if send_as_email == bare_alias and status in ("accepted", "verified", ""):
            return from_alias
    raise SafetyError(
        "UNAUTHORIZED_SEND_AS_ALIAS",
        f"Alias '{from_alias}' is not a verified send-as address on this Gmail account.",
        {"requested_alias": from_alias},
    )


async def _fetch_reply_target_metadata(
    client: GmailClientWrapper,
    *,
    thread_id: str | None,
    message_id: str | None,
    tool_name: str,
) -> tuple[dict[str, Any], int]:
    """Fetch source message metadata (`Message-ID`, `References`, `Subject`, `From`, `To`, `Cc`, `threadId`)."""
    units_used = 0
    if message_id:
        msg_resp, m_meta = await client.execute_api(
            "messages.get",
            {
                "id": message_id,
                "format": "metadata",
                "metadataHeaders": DEFAULT_METADATA_HEADERS,
            },
            tool_name=tool_name,
            op_class="read",
            fmt="metadata",
        )
        units_used += int(m_meta.get("quota_estimate", 20))
        parsed = client.mime_builder.parse_gmail_payload(msg_resp)
        resolved_thread_id = parsed.get("thread_id") or thread_id
        if thread_id and parsed.get("thread_id") and parsed["thread_id"] != thread_id:
            raise SafetyError(
                "THREAD_MESSAGE_MISMATCH",
                f"Message '{message_id}' belongs to thread '{parsed['thread_id']}', not '{thread_id}'.",
            )
        parsed["thread_id"] = resolved_thread_id
        return parsed, units_used

    if thread_id:
        th_resp, t_meta = await client.execute_api(
            "threads.get",
            {
                "id": thread_id,
                "format": "metadata",
                "metadataHeaders": DEFAULT_METADATA_HEADERS,
            },
            tool_name=tool_name,
            op_class="read",
            fmt="metadata",
        )
        units_used += int(t_meta.get("quota_estimate", 40))
        msgs = th_resp.get("messages") or []
        if not msgs:
            raise SafetyError("EMPTY_THREAD", f"Thread '{thread_id}' contains no messages.")
        last_msg = client.mime_builder.parse_gmail_payload(msgs[-1])
        last_msg["thread_id"] = th_resp.get("id") or thread_id
        return last_msg, units_used

    raise SafetyError("MISSING_REPLY_TARGET", "Either `message_id` or `thread_id` is required to send a reply.")


async def gmail_send_email(
    client: GmailClientWrapper,
    to: list[str] | str,
    subject: str | None = None,
    *,
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
    """
    Send a single email (requires `confirm_send=True`; returns a full preview if `confirm_send=False` or `dry_run=True`).
    Enforces recipient validation, BCC privacy, idempotency protection, daily send caps, and ambiguous-timeout reconciliation.
    """
    try:
        verified_alias = await _validate_from_alias(client, from_alias, "gmail_send_email")
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
            from_address=verified_alias,
            subject=resolved["subject"],
            body_text=resolved["body_text"],
            body_html=resolved["body_html"],
            custom_headers=custom_headers,
            attachments=resolved["attachments"],
        )
        recips = built["recipients"]
        idem_key = idempotency_key or _compute_default_idempotency_key(
            "send_email",
            recips["to"],
            recips["cc"],
            recips["bcc"],
            resolved["subject"],
            resolved["body_text"],
            resolved["body_html"],
            None,
            resolved["attachments"],
        )

        if dry_run or not confirm_send:
            headroom = client.quota_manager.get_sending_headroom(
                requested_messages=1,
                requested_recipients=recips["total_count"],
            )
            return {
                "success": True,
                "operation": "gmail_send_email",
                "mutates_mailbox": False,
                "status": "preview_requires_confirmation",
                "requires_confirmation": not confirm_send,
                "preview": {
                    "recipients": recips["public_summary"],
                    "subject": resolved["subject"],
                    "body_text_preview": (resolved["body_text"] or "")[:300],
                    "has_html": resolved["body_html"] is not None,
                    "attachments": built["attachments"],
                    "encoded_size_bytes": built["encoded_size_bytes"],
                },
                "sending_headroom": headroom,
                "idempotency_key": idem_key,
                "warnings": built["warnings"],
                "quota_estimate": {"units_per_request": 100, "total_units": 0},
            }

        resp, meta = await client.send_raw_mime_safely(
            raw_b64=built["raw"],
            rfc_message_id=built["rfc_message_id"],
            recipient_summary=", ".join(recips["to"]),
            operation_type="send_email",
            idempotency_key=idem_key,
            tool_name="gmail_send_email",
            cc_count=len(recips["cc"]),
            bcc_count=recips["bcc_count"],
            total_recipient_count=recips["total_count"],
            attachment_bytes=built["raw_mime_size_bytes"],
        )
        return {
            "success": True,
            "operation": "gmail_send_email",
            "mutates_mailbox": not bool(resp.get("deduplicated")),
            "status": "deduplicated" if resp.get("deduplicated") else "sent",
            "deduplicated": bool(resp.get("deduplicated")),
            "message_id": resp.get("id"),
            "thread_id": resp.get("threadId"),
            "rfc_message_id": built["rfc_message_id"],
            "idempotency_key": idem_key,
            "recipients": recips["public_summary"],
            "subject": resolved["subject"],
            "attachments": built["attachments"],
            "warnings": built["warnings"],
            "quota_estimate": {
                "units_per_request": 100,
                "total_units": int(meta.get("quota_estimate", 100)),
            },
        }
    except Exception as exc:
        return client.format_tool_error("gmail_send_email", exc, mutates_mailbox=bool(confirm_send and not dry_run))


async def gmail_send_reply(
    client: GmailClientWrapper,
    thread_id: str | None = None,
    message_id: str | None = None,
    *,
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
    """
    Reply to a specific email inside its conversation thread.
    Automatically extracts `Message-ID`, `In-Reply-To`, `References`, and `Subject` (`Re: ...`),
    sets `threadId`, and sends when `confirm_send=True`.
    """
    try:
        source_meta, fetch_units = await _fetch_reply_target_metadata(
            client, thread_id=thread_id, message_id=message_id, tool_name="gmail_send_reply"
        )
        reply_hdrs = client.mime_builder.build_reply_headers(source_meta["headers"])
        target_thread_id = source_meta["thread_id"]
        reply_to_addr = reply_hdrs["reply_to_address"]
        if not reply_to_addr:
            raise SafetyError("MISSING_REPLY_RECIPIENT", "Source message has no From or Reply-To address.")

        resolved = resolve_content_or_template(
            client,
            subject=reply_hdrs["subject"],
            body_text=body_text,
            body_html=body_html,
            template_id=template_id,
            variables=variables,
            attachments=attachments,
        )
        # Keep the thread subject matching the original conversation subject
        final_subject = reply_hdrs["subject"]

        built = client.mime_builder.build_email_message(
            to=reply_to_addr,
            cc=cc,
            bcc=bcc,
            subject=final_subject,
            body_text=resolved["body_text"],
            body_html=resolved["body_html"],
            in_reply_to=reply_hdrs["in_reply_to"],
            references=reply_hdrs["references"],
            attachments=resolved["attachments"],
        )
        recips = built["recipients"]
        idem_key = idempotency_key or _compute_default_idempotency_key(
            "send_reply",
            recips["to"],
            recips["cc"],
            recips["bcc"],
            final_subject,
            resolved["body_text"],
            resolved["body_html"],
            target_thread_id,
            resolved["attachments"],
        )

        if dry_run or not confirm_send:
            return {
                "success": True,
                "operation": "gmail_send_reply",
                "mutates_mailbox": False,
                "status": "preview_requires_confirmation",
                "requires_confirmation": not confirm_send,
                "thread_id": target_thread_id,
                "source_message_id": source_meta.get("id"),
                "headers_constructed": {
                    "Subject": final_subject,
                    "In-Reply-To": reply_hdrs["in_reply_to"],
                    "References": reply_hdrs["references"],
                },
                "recipients": recips["public_summary"],
                "idempotency_key": idem_key,
                "quota_estimate": {"units_per_request": 100, "total_units": fetch_units},
            }

        resp, send_meta = await client.send_raw_mime_safely(
            raw_b64=built["raw"],
            rfc_message_id=built["rfc_message_id"],
            recipient_summary=", ".join(recips["to"]),
            operation_type="send_reply",
            idempotency_key=idem_key,
            tool_name="gmail_send_reply",
            thread_id=target_thread_id,
            cc_count=len(recips["cc"]),
            bcc_count=recips["bcc_count"],
            total_recipient_count=recips["total_count"],
            attachment_bytes=built["raw_mime_size_bytes"],
        )
        total_units = fetch_units + int(send_meta.get("quota_estimate", 100))
        return {
            "success": True,
            "operation": "gmail_send_reply",
            "mutates_mailbox": not bool(resp.get("deduplicated")),
            "status": "deduplicated" if resp.get("deduplicated") else "sent",
            "deduplicated": bool(resp.get("deduplicated")),
            "message_id": resp.get("id"),
            "thread_id": resp.get("threadId") or target_thread_id,
            "in_reply_to": reply_hdrs["in_reply_to"],
            "references": reply_hdrs["references"],
            "subject": final_subject,
            "recipients": recips["public_summary"],
            "idempotency_key": idem_key,
            "quota_estimate": {"units_per_request": 120, "total_units": total_units},
        }
    except Exception as exc:
        return client.format_tool_error("gmail_send_reply", exc, mutates_mailbox=bool(confirm_send and not dry_run))


async def gmail_send_reply_all(
    client: GmailClientWrapper,
    thread_id: str | None = None,
    message_id: str | None = None,
    *,
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
    """Reply to all appropriate recipients (sender + original To/Cc minus authenticated user) inside the original thread."""
    try:
        source_meta, fetch_units = await _fetch_reply_target_metadata(
            client, thread_id=thread_id, message_id=message_id, tool_name="gmail_send_reply_all"
        )
        prof, p_meta = await client.get_profile(tool_name="gmail_send_reply_all", use_cache=True)
        fetch_units += int(p_meta.get("quota_estimate", 0))
        self_email = (prof.get("emailAddress") or "").lower().strip()

        reply_hdrs = client.mime_builder.build_reply_headers(source_meta["headers"])
        primary_to = reply_hdrs["reply_to_address"]
        raw_others = [
            addr.strip()
            for part in (reply_hdrs["original_to"], reply_hdrs["original_cc"])
            for addr in part.split(",")
            if addr.strip()
        ]
        filtered_cc: list[str] = []
        for candidate in raw_others:
            try:
                bare = client.safety.extract_email_address(candidate).lower()
                if bare != self_email:
                    filtered_cc.append(candidate)
            except SafetyError:
                continue

        return await gmail_send_reply(
            client,
            thread_id=source_meta["thread_id"],
            message_id=source_meta.get("id") or message_id,
            body_text=body_text,
            body_html=body_html,
            template_id=template_id,
            variables=variables,
            attachments=attachments,
            cc=filtered_cc,
            bcc=bcc,
            idempotency_key=idempotency_key,
            confirm_send=confirm_send,
            dry_run=dry_run,
        )
    except Exception as exc:
        return client.format_tool_error("gmail_send_reply_all", exc, mutates_mailbox=bool(confirm_send and not dry_run))


async def gmail_forward_message(
    client: GmailClientWrapper,
    message_id: str,
    to: list[str] | str,
    *,
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
    """
    Forward an existing email to one or multiple approved recipients,
    preserving relevant original content and attachments where requested.
    """
    try:
        resp, g_meta = await client.execute_api(
            "messages.get",
            {"id": message_id, "format": "raw"},
            tool_name="gmail_forward_message",
            op_class="read",
            fmt="raw",
        )
        fetch_units = int(g_meta.get("quota_estimate", 60))
        parsed = client.mime_builder.parse_raw_mime(resp.get("raw", ""))

        orig_subj = parsed["subject"].strip()
        fwd_subject = orig_subj if orig_subj.lower().startswith("fwd:") else f"Fwd: {orig_subj}"

        fwd_banner = (
            "---------- Forwarded message ---------\n"
            f"From: {parsed['from']}\n"
            f"Date: {parsed['headers'].get('Date', '')}\n"
            f"Subject: {orig_subj}\n"
            f"To: {', '.join(parsed['to'])}\n\n"
        )
        orig_text = parsed["body_text"] or ""
        combined_text = f"{comment_text}\n\n{fwd_banner}{orig_text}".strip() if comment_text else f"{fwd_banner}{orig_text}"

        combined_html: str | None = None
        if parsed["body_html"] or comment_html:
            html_banner = (
                "<br><hr><b>---------- Forwarded message ---------</b><br>"
                f"<b>From:</b> {parsed['from']}<br>"
                f"<b>Subject:</b> {orig_subj}<br><br>"
            )
            combined_html = f"{comment_html or ''}{html_banner}{parsed['body_html'] or ''}"

        preserved_blobs = parsed["attachments"] if include_original_attachments else []
        built = client.mime_builder.build_email_message(
            to=to,
            cc=cc,
            bcc=bcc,
            subject=fwd_subject,
            body_text=combined_text,
            body_html=combined_html,
            attachments=additional_attachments,
            extra_attachment_blobs=preserved_blobs,
        )
        recips = built["recipients"]
        idem_key = idempotency_key or _compute_default_idempotency_key(
            f"forward_{message_id}",
            recips["to"],
            recips["cc"],
            recips["bcc"],
            fwd_subject,
            combined_text,
            combined_html,
            None,
            list(additional_attachments or []),
        )

        if dry_run or not confirm_send:
            return {
                "success": True,
                "operation": "gmail_forward_message",
                "mutates_mailbox": False,
                "status": "preview_requires_confirmation",
                "requires_confirmation": not confirm_send,
                "source_message_id": message_id,
                "subject": fwd_subject,
                "recipients": recips["public_summary"],
                "attachments_included": [a["filename"] for a in built["attachments"]],
                "idempotency_key": idem_key,
                "quota_estimate": {"units_per_request": 160, "total_units": fetch_units},
            }

        send_resp, s_meta = await client.send_raw_mime_safely(
            raw_b64=built["raw"],
            rfc_message_id=built["rfc_message_id"],
            recipient_summary=", ".join(recips["to"]),
            operation_type="forward_message",
            idempotency_key=idem_key,
            tool_name="gmail_forward_message",
            cc_count=len(recips["cc"]),
            bcc_count=recips["bcc_count"],
            total_recipient_count=recips["total_count"],
            attachment_bytes=built["raw_mime_size_bytes"],
        )
        total_units = fetch_units + int(s_meta.get("quota_estimate", 100))
        return {
            "success": True,
            "operation": "gmail_forward_message",
            "mutates_mailbox": not bool(send_resp.get("deduplicated")),
            "status": "deduplicated" if send_resp.get("deduplicated") else "sent",
            "source_message_id": message_id,
            "message_id": send_resp.get("id"),
            "thread_id": send_resp.get("threadId"),
            "subject": fwd_subject,
            "recipients": recips["public_summary"],
            "attachments": built["attachments"],
            "idempotency_key": idem_key,
            "quota_estimate": {"units_per_request": 160, "total_units": total_units},
        }
    except Exception as exc:
        return client.format_tool_error("gmail_forward_message", exc, mutates_mailbox=bool(confirm_send and not dry_run))


# --- Two-Phase Bulk Sending & Bulk Replies ---


def _load_recipient_records(
    client: GmailClientWrapper,
    recipients: list[dict[str, Any] | str] | None,
    recipient_source: str | None,
) -> list[dict[str, Any]]:
    """Load recipient records from an inline list or a CSV/JSON file inside allowed directories."""
    records: list[dict[str, Any]] = []
    if recipients:
        for item in recipients:
            if isinstance(item, str):
                records.append({"email": item})
            elif isinstance(item, dict):
                records.append(dict(item))
            else:
                raise SafetyError("INVALID_RECIPIENT_RECORD", f"Invalid recipient item: {item}")

    if recipient_source:
        resolved = client.safety.resolve_safe_path(recipient_source, must_exist=True)
        suffix = resolved.suffix.lower()
        if suffix == ".csv":
            with resolved.open("r", encoding="utf-8-sig", newline="") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    records.append({k.strip(): v for k, v in row.items() if k})
        elif suffix == ".json":
            loaded = json.loads(resolved.read_text(encoding="utf-8"))
            if not isinstance(loaded, list):
                raise SafetyError("INVALID_RECIPIENT_SOURCE", "JSON recipient_source must contain a JSON array.")
            for item in loaded:
                records.append({"email": item} if isinstance(item, str) else dict(item))
        else:
            raise SafetyError("UNSUPPORTED_RECIPIENT_SOURCE", "recipient_source must be a .csv or .json file.")

    if not records:
        raise SafetyError("EMPTY_RECIPIENT_LIST", "Provide a non-empty `recipients` list or `recipient_source` file.")
    return records


async def gmail_prepare_bulk_send(
    client: GmailClientWrapper,
    recipients: list[dict[str, Any] | str] | None = None,
    *,
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
    """
    Phase 1 of two-phase bulk sending:
    1. Validate recipients and content
    2. Estimate quota consumption (100 units per email)
    3. Check daily sending headroom against configured 200/300 caps
    4. Persist immutable plan with SHA-256 hash in SQLite
    5. Return complete preview and required confirmation phrase (`CONFIRM SEND N EMAILS`).
    Never sends any email.
    """
    try:
        raw_records = _load_recipient_records(client, recipients, recipient_source)
        shared_attachments = list(attachments or [])

        if reconcile_sent_mail:
            await client.reconcile_sent_mail_24h(tool_name="gmail_prepare_bulk_send")

        prepared_items: list[dict[str, Any]] = []
        preview_items: list[dict[str, Any]] = []
        total_recipients_across_plan = 0

        reserved = {
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
        }

        for idx, rec in enumerate(raw_records):
            to_addr = rec.get("email") or rec.get("to")
            cc_addr = rec.get("cc")
            bcc_addr = rec.get("bcc")
            item_tpl = rec.get("template_id") or template_id
            item_subj = rec.get("subject") if rec.get("subject") is not None else subject
            item_text = rec.get("body_text") if rec.get("body_text") is not None else body_text
            item_html = rec.get("body_html") if rec.get("body_html") is not None else body_html
            item_atts = list(dict.fromkeys(shared_attachments + list(rec.get("attachments") or [])))

            vars_map: dict[str, Any] = {}
            if personalize:
                vars_map = {k: v for k, v in rec.items() if k not in reserved}
                if isinstance(rec.get("variables"), dict):
                    vars_map.update(rec["variables"])

            resolved = resolve_content_or_template(
                client,
                subject=item_subj,
                body_text=item_text,
                body_html=item_html,
                template_id=item_tpl,
                variables=vars_map,
                attachments=item_atts,
            )
            # Validate MIME build & recipient addresses during preparation
            built = client.mime_builder.build_email_message(
                to=to_addr,
                cc=cc_addr,
                bcc=bcc_addr,
                subject=resolved["subject"],
                body_text=resolved["body_text"],
                body_html=resolved["body_html"],
                attachments=resolved["attachments"],
            )
            rec_info = built["recipients"]
            total_recipients_across_plan += rec_info["total_count"]

            normalized_item = {
                "index": idx,
                "to": rec_info["to"],
                "cc": rec_info["cc"],
                "bcc": rec_info["bcc"],
                "subject": resolved["subject"],
                "body_text": resolved["body_text"],
                "body_html": resolved["body_html"],
                "attachments": [str(a.get("path") or a["filename"]) for a in built["attachments"]],
                "total_recipient_count": rec_info["total_count"],
            }
            prepared_items.append(normalized_item)
            preview_items.append(
                {
                    "index": idx,
                    "recipients": rec_info["public_summary"],
                    "subject": resolved["subject"],
                    "body_preview": (resolved["body_text"] or resolved["body_html"] or "")[:160],
                    "attachments": [a["filename"] for a in built["attachments"]],
                    "encoded_size_bytes": built["encoded_size_bytes"],
                }
            )

        count = len(prepared_items)
        estimated_quota = count * 100
        headroom = client.quota_manager.get_sending_headroom(
            requested_messages=count,
            requested_recipients=total_recipients_across_plan,
        )
        confirmation_phrase = f"CONFIRM SEND {count} EMAILS"

        plan = client.bulk_plan_repo.create_plan(
            plan_type="bulk_send",
            items=prepared_items,
            confirmation_phrase=confirmation_phrase,
            total_recipient_count=total_recipients_across_plan,
            estimated_quota_units=estimated_quota,
        )

        return {
            "success": True,
            "operation": "prepare_bulk_send",
            "mutates_mailbox": False,
            "dry_run": dry_run,
            "plan_id": plan.plan_id,
            "plan_hash": plan.plan_hash,
            "status": plan.status,
            "requested_count": count,
            "total_recipient_count": total_recipients_across_plan,
            "required_confirmation_phrase": confirmation_phrase,
            "expires_at": plan.expires_at,
            "sending_headroom": headroom,
            "preview_items": preview_items,
            "quota_estimate": {
                "units_per_request": 100,
                "total_units": estimated_quota,
            },
        }
    except Exception as exc:
        return client.format_tool_error("prepare_bulk_send", exc, mutates_mailbox=False)


async def gmail_execute_bulk_send(
    client: GmailClientWrapper,
    plan_id: str,
    confirmation_phrase: str,
    *,
    modified_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Phase 2 of two-phase bulk sending:
    Verifies `plan_id`, exact `confirmation_phrase` (`CONFIRM SEND <N> EMAILS`), and SHA-256 plan integrity.
    Executes sequentially (`send_concurrency=1`, `send_interval_seconds=3`), persists individual outcomes in SQLite,
    prevents duplicate sends on resumption, and halts immediately if the daily sending threshold is reached.
    """
    try:
        plan = client.bulk_plan_repo.get(plan_id)
        if not plan or plan.plan_type != "bulk_send":
            return {
                "success": False,
                "operation": "bulk_send",
                "mutates_mailbox": False,
                "error_code": "PLAN_NOT_FOUND",
                "message": f"Bulk send plan '{plan_id}' was not found.",
                "http_status": 404,
                "retryable": False,
            }

        if modified_items is not None:
            client.bulk_plan_repo.update_status(plan_id, "INVALIDATED")
            return {
                "success": False,
                "operation": "bulk_send",
                "mutates_mailbox": False,
                "error_code": "PLAN_INTEGRITY_VIOLATION",
                "message": "Modifying recipients or content during execution invalidates the approved plan. Prepare a new plan.",
                "http_status": 400,
                "retryable": False,
            }

        if not client.bulk_plan_repo.verify_integrity(plan):
            client.bulk_plan_repo.update_status(plan_id, "INVALIDATED")
            return {
                "success": False,
                "operation": "bulk_send",
                "mutates_mailbox": False,
                "error_code": "PLAN_INTEGRITY_VIOLATION",
                "message": f"Stored plan '{plan_id}' failed SHA-256 integrity verification.",
                "http_status": 400,
                "retryable": False,
            }

        if plan.status == "INVALIDATED":
            return {
                "success": False,
                "operation": "bulk_send",
                "mutates_mailbox": False,
                "error_code": "PLAN_INVALIDATED",
                "message": f"Plan '{plan_id}' has been invalidated and cannot be executed.",
                "http_status": 400,
                "retryable": False,
            }

        expected_phrase = plan.confirmation_phrase
        if (confirmation_phrase or "").strip() != expected_phrase:
            return {
                "success": False,
                "operation": "bulk_send",
                "mutates_mailbox": False,
                "error_code": "CONFIRMATION_PHRASE_MISMATCH",
                "message": f"Invalid confirmation phrase. Expected exact phrase: '{expected_phrase}'.",
                "http_status": 400,
                "retryable": False,
            }

        exp_dt = datetime.fromisoformat(plan.expires_at)
        if datetime.now(timezone.utc) > exp_dt:
            return {
                "success": False,
                "operation": "bulk_send",
                "mutates_mailbox": False,
                "error_code": "PLAN_EXPIRED",
                "message": f"Bulk send plan '{plan_id}' expired at {plan.expires_at}. Prepare a fresh plan.",
                "http_status": 400,
                "retryable": False,
            }

        client.bulk_plan_repo.update_status(plan_id, "EXECUTING")

        requested = plan.requested_count
        sent_count = 0
        skipped_already_sent = 0
        failed_count = 0
        total_units_consumed = 0
        individual_results: list[dict[str, Any]] = []
        retryable_failures: list[dict[str, Any]] = []
        non_retryable_failures: list[dict[str, Any]] = []
        stopped_error_code: str | None = None
        stopped_error_message: str | None = None
        resumable = False

        for item in plan.items:
            idx = int(item["index"])
            recip_list = item["to"]
            primary_recip = ", ".join(recip_list)
            idem_key = f"{plan_id}:item:{idx}:{primary_recip.lower()}"

            # Check if already sent in a prior partial run of this plan
            existing_ledger = client.send_ledger_repo.get_by_idempotency_key(idem_key)
            if existing_ledger and existing_ledger.status in ("SENT", "INDETERMINATE"):
                skipped_already_sent += 1
                sent_count += 1
                individual_results.append(
                    {
                        "index": idx,
                        "recipient": primary_recip,
                        "message_id": existing_ledger.gmail_message_id,
                        "status": "already_sent_skipped",
                        "deduplicated": True,
                    }
                )
                continue

            try:
                built = client.mime_builder.build_email_message(
                    to=item["to"],
                    cc=item.get("cc"),
                    bcc=item.get("bcc"),
                    subject=item["subject"],
                    body_text=item.get("body_text"),
                    body_html=item.get("body_html"),
                    attachments=item.get("attachments"),
                )
                resp, meta = await client.send_raw_mime_safely(
                    raw_b64=built["raw"],
                    rfc_message_id=built["rfc_message_id"],
                    recipient_summary=primary_recip,
                    operation_type="bulk_send",
                    idempotency_key=idem_key,
                    tool_name="gmail_execute_bulk_send",
                    cc_count=len(item.get("cc") or []),
                    bcc_count=len(item.get("bcc") or []),
                    total_recipient_count=int(item.get("total_recipient_count") or 1),
                    plan_id=plan_id,
                    attachment_bytes=built["raw_mime_size_bytes"],
                )
                total_units_consumed += int(meta.get("quota_estimate", 100))
                sent_count += 1
                individual_results.append(
                    {
                        "index": idx,
                        "recipient": primary_recip,
                        "message_id": resp.get("id"),
                        "thread_id": resp.get("threadId"),
                        "status": "sent",
                    }
                )
            except (QuotaExhaustedError, GmailMCPError, SafetyError) as item_exc:
                err_dict = client.format_tool_error("bulk_send", item_exc, mutates_mailbox=True)
                err_code = err_dict["error_code"]
                err_msg = err_dict["message"]

                # Stop immediately on daily sending limit or quota exhaustion without recording the unattempted item as failed
                if err_code in (
                    "DAILY_SEND_LIMIT",
                    "QUOTA_EXHAUSTED",
                    "USER_RATE_LIMIT_HEADROOM_EXCEEDED",
                    "PROJECT_RATE_LIMIT_HEADROOM_EXCEEDED",
                ):
                    stopped_error_code = err_code
                    stopped_error_message = err_msg
                    resumable = True
                    client.bulk_plan_repo.update_status(plan_id, "PAUSED_LIMIT_REACHED")
                    break

                failed_count += 1
                fail_entry = {
                    "index": idx,
                    "recipient": primary_recip,
                    "status": "failed",
                    "error_code": err_code,
                    "message": err_msg,
                    "retryable": err_dict["retryable"],
                }
                individual_results.append(fail_entry)
                if err_dict["retryable"]:
                    retryable_failures.append(fail_entry)
                else:
                    non_retryable_failures.append(fail_entry)

        remaining = max(0, requested - sent_count - failed_count)

        if stopped_error_code is not None:
            return {
                "success": False,
                "operation": "bulk_send",
                "mutates_mailbox": sent_count > 0,
                "plan_id": plan_id,
                "error_code": stopped_error_code,
                "message": stopped_error_message or "The configured sending threshold has been reached.",
                "requested": requested,
                "successful": sent_count,
                "sent": sent_count,
                "failed": failed_count,
                "skipped": skipped_already_sent + remaining,
                "remaining": remaining,
                "resumable": resumable,
                "safe_to_continue": False,
                "individual_results": individual_results,
                "retryable_failures": retryable_failures,
                "non_retryable_failures": non_retryable_failures,
                "quota_estimate": {
                    "units_per_request": 100,
                    "total_units": total_units_consumed,
                },
            }

        all_succeeded = (sent_count == requested and failed_count == 0)
        final_status = "COMPLETED" if all_succeeded else "PARTIAL_FAILURE"
        client.bulk_plan_repo.update_status(plan_id, final_status)

        return {
            "success": all_succeeded,
            "operation": "bulk_send",
            "mutates_mailbox": sent_count > 0,
            "plan_id": plan_id,
            "status": final_status,
            "requested": requested,
            "successful": sent_count,
            "sent": sent_count,
            "failed": failed_count,
            "skipped": skipped_already_sent,
            "remaining": remaining,
            "resumable": failed_count > 0 or remaining > 0,
            "safe_to_continue": True,
            "individual_results": individual_results,
            "retryable_failures": retryable_failures,
            "non_retryable_failures": non_retryable_failures,
            "quota_estimate": {
                "units_per_request": 100,
                "total_units": total_units_consumed,
            },
        }
    except Exception as exc:
        return client.format_tool_error("bulk_send", exc, mutates_mailbox=True)


async def gmail_prepare_bulk_reply(
    client: GmailClientWrapper,
    replies: list[dict[str, Any]],
    *,
    template_id: str | None = None,
    attachments: list[str] | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """
    Phase 1 of two-phase bulk replies:
    For each reply item (up to 100+ threads):
    1. Retrieve source message or thread metadata
    2. Identify the appropriate `threadId`
    3. Extract `Message-ID`, `In-Reply-To`, `References`, and `Subject`
    4. Construct valid RFC reply headers
    5. Estimate quota and check daily sending headroom
    6. Persist plan in SQLite with required confirmation phrase (`CONFIRM SEND <N> REPLIES`).
    """
    try:
        if not replies:
            raise SafetyError("EMPTY_REPLY_LIST", "`replies` list cannot be empty.")

        shared_attachments = list(attachments or [])
        prepared_items: list[dict[str, Any]] = []
        preview_items: list[dict[str, Any]] = []
        metadata_units_used = 0

        for idx, item in enumerate(replies):
            th_id = item.get("thread_id")
            msg_id = item.get("message_id")
            source_meta, u_used = await _fetch_reply_target_metadata(
                client, thread_id=th_id, message_id=msg_id, tool_name="gmail_prepare_bulk_reply"
            )
            metadata_units_used += u_used

            reply_hdrs = client.mime_builder.build_reply_headers(source_meta["headers"])
            target_thread_id = source_meta["thread_id"]
            to_addr = item.get("to") or reply_hdrs["reply_to_address"]
            if not to_addr:
                raise SafetyError(
                    "MISSING_REPLY_RECIPIENT",
                    f"Reply item #{idx} (thread={target_thread_id}) has no valid recipient.",
                )

            item_tpl = item.get("template_id") or template_id
            item_atts = list(dict.fromkeys(shared_attachments + list(item.get("attachments") or [])))
            resolved = resolve_content_or_template(
                client,
                subject=reply_hdrs["subject"],
                body_text=item.get("body_text"),
                body_html=item.get("body_html"),
                template_id=item_tpl,
                variables=item.get("variables"),
                attachments=item_atts,
            )

            built = client.mime_builder.build_email_message(
                to=to_addr,
                cc=item.get("cc"),
                bcc=item.get("bcc"),
                subject=reply_hdrs["subject"],
                body_text=resolved["body_text"],
                body_html=resolved["body_html"],
                in_reply_to=reply_hdrs["in_reply_to"],
                references=reply_hdrs["references"],
                attachments=resolved["attachments"],
            )
            rec_info = built["recipients"]

            norm_item = {
                "index": idx,
                "thread_id": target_thread_id,
                "source_message_id": source_meta.get("id") or msg_id,
                "to": rec_info["to"],
                "cc": rec_info["cc"],
                "bcc": rec_info["bcc"],
                "subject": reply_hdrs["subject"],
                "in_reply_to": reply_hdrs["in_reply_to"],
                "references": reply_hdrs["references"],
                "body_text": resolved["body_text"],
                "body_html": resolved["body_html"],
                "attachments": [str(a.get("path") or a["filename"]) for a in built["attachments"]],
                "total_recipient_count": rec_info["total_count"],
            }
            prepared_items.append(norm_item)
            preview_items.append(
                {
                    "index": idx,
                    "thread_id": target_thread_id,
                    "source_message_id": norm_item["source_message_id"],
                    "recipients": rec_info["public_summary"],
                    "subject": reply_hdrs["subject"],
                    "in_reply_to": reply_hdrs["in_reply_to"],
                    "references": reply_hdrs["references"],
                    "body_preview": (resolved["body_text"] or resolved["body_html"] or "")[:160],
                }
            )

        count = len(prepared_items)
        estimated_send_quota = count * 100
        headroom = client.quota_manager.get_sending_headroom(
            requested_messages=count,
            requested_recipients=count,
        )
        confirmation_phrase = f"CONFIRM SEND {count} REPLIES"

        plan = client.bulk_plan_repo.create_plan(
            plan_type="bulk_reply",
            items=prepared_items,
            confirmation_phrase=confirmation_phrase,
            total_recipient_count=count,
            estimated_quota_units=estimated_send_quota,
        )

        return {
            "success": True,
            "operation": "prepare_bulk_reply",
            "mutates_mailbox": False,
            "dry_run": dry_run,
            "plan_id": plan.plan_id,
            "plan_hash": plan.plan_hash,
            "status": plan.status,
            "requested_count": count,
            "required_confirmation_phrase": confirmation_phrase,
            "expires_at": plan.expires_at,
            "sending_headroom": headroom,
            "preview_items": preview_items,
            "quota_estimate": {
                "units_consumed_preparing": metadata_units_used,
                "estimated_send_units": estimated_send_quota,
                "total_units": metadata_units_used + estimated_send_quota,
            },
        }
    except Exception as exc:
        return client.format_tool_error("prepare_bulk_reply", exc, mutates_mailbox=False)


async def gmail_execute_bulk_reply(
    client: GmailClientWrapper,
    plan_id: str,
    confirmation_phrase: str,
) -> dict[str, Any]:
    """
    Phase 2 of two-phase bulk replies:
    Verifies `plan_id`, `CONFIRM SEND <N> REPLIES`, and SHA-256 plan integrity,
    then sends each reply inside its respective thread with RFC threading headers.
    """
    try:
        plan = client.bulk_plan_repo.get(plan_id)
        if not plan or plan.plan_type != "bulk_reply":
            return {
                "success": False,
                "operation": "bulk_reply",
                "mutates_mailbox": False,
                "error_code": "PLAN_NOT_FOUND",
                "message": f"Bulk reply plan '{plan_id}' was not found.",
                "http_status": 404,
                "retryable": False,
            }

        if not client.bulk_plan_repo.verify_integrity(plan):
            client.bulk_plan_repo.update_status(plan_id, "INVALIDATED")
            return {
                "success": False,
                "operation": "bulk_reply",
                "mutates_mailbox": False,
                "error_code": "PLAN_INTEGRITY_VIOLATION",
                "message": f"Stored bulk reply plan '{plan_id}' failed SHA-256 integrity verification.",
                "http_status": 400,
                "retryable": False,
            }

        if (confirmation_phrase or "").strip() != plan.confirmation_phrase:
            return {
                "success": False,
                "operation": "bulk_reply",
                "mutates_mailbox": False,
                "error_code": "CONFIRMATION_PHRASE_MISMATCH",
                "message": f"Invalid confirmation phrase. Expected exact phrase: '{plan.confirmation_phrase}'.",
                "http_status": 400,
                "retryable": False,
            }

        client.bulk_plan_repo.update_status(plan_id, "EXECUTING")

        requested = plan.requested_count
        sent_count = 0
        skipped_count = 0
        failed_count = 0
        total_units_consumed = 0
        individual_results: list[dict[str, Any]] = []
        retryable_failures: list[dict[str, Any]] = []
        non_retryable_failures: list[dict[str, Any]] = []
        stopped_error_code: str | None = None
        stopped_error_message: str | None = None

        for item in plan.items:
            idx = int(item["index"])
            th_id = item["thread_id"]
            primary_recip = ", ".join(item["to"])
            idem_key = f"{plan_id}:reply:{idx}:{th_id}"

            existing = client.send_ledger_repo.get_by_idempotency_key(idem_key)
            if existing and existing.status in ("SENT", "INDETERMINATE"):
                skipped_count += 1
                sent_count += 1
                individual_results.append(
                    {
                        "index": idx,
                        "thread_id": th_id,
                        "recipient": primary_recip,
                        "message_id": existing.gmail_message_id,
                        "status": "already_sent_skipped",
                    }
                )
                continue

            try:
                built = client.mime_builder.build_email_message(
                    to=item["to"],
                    cc=item.get("cc"),
                    bcc=item.get("bcc"),
                    subject=item["subject"],
                    body_text=item.get("body_text"),
                    body_html=item.get("body_html"),
                    in_reply_to=item.get("in_reply_to"),
                    references=item.get("references"),
                    attachments=item.get("attachments"),
                )
                resp, meta = await client.send_raw_mime_safely(
                    raw_b64=built["raw"],
                    rfc_message_id=built["rfc_message_id"],
                    recipient_summary=primary_recip,
                    operation_type="bulk_reply",
                    idempotency_key=idem_key,
                    tool_name="gmail_execute_bulk_reply",
                    thread_id=th_id,
                    cc_count=len(item.get("cc") or []),
                    bcc_count=len(item.get("bcc") or []),
                    total_recipient_count=int(item.get("total_recipient_count") or 1),
                    plan_id=plan_id,
                    attachment_bytes=built["raw_mime_size_bytes"],
                )
                total_units_consumed += int(meta.get("quota_estimate", 100))
                sent_count += 1
                individual_results.append(
                    {
                        "index": idx,
                        "thread_id": resp.get("threadId") or th_id,
                        "message_id": resp.get("id"),
                        "recipient": primary_recip,
                        "in_reply_to": item.get("in_reply_to"),
                        "status": "sent",
                    }
                )
            except (QuotaExhaustedError, GmailMCPError, SafetyError) as item_exc:
                err_dict = client.format_tool_error("bulk_reply", item_exc, mutates_mailbox=True)
                err_code = err_dict["error_code"]
                if err_code in (
                    "DAILY_SEND_LIMIT",
                    "QUOTA_EXHAUSTED",
                    "USER_RATE_LIMIT_HEADROOM_EXCEEDED",
                    "PROJECT_RATE_LIMIT_HEADROOM_EXCEEDED",
                ):
                    stopped_error_code = err_code
                    stopped_error_message = err_dict["message"]
                    client.bulk_plan_repo.update_status(plan_id, "PAUSED_LIMIT_REACHED")
                    break

                failed_count += 1
                fail_entry = {
                    "index": idx,
                    "thread_id": th_id,
                    "recipient": primary_recip,
                    "status": "failed",
                    "error_code": err_code,
                    "message": err_dict["message"],
                    "retryable": err_dict["retryable"],
                }
                individual_results.append(fail_entry)
                if err_dict["retryable"]:
                    retryable_failures.append(fail_entry)
                else:
                    non_retryable_failures.append(fail_entry)

        remaining = max(0, requested - sent_count - failed_count)
        if stopped_error_code is not None:
            return {
                "success": False,
                "operation": "bulk_reply",
                "mutates_mailbox": sent_count > 0,
                "plan_id": plan_id,
                "error_code": stopped_error_code,
                "message": stopped_error_message,
                "requested": requested,
                "successful": sent_count,
                "sent": sent_count,
                "failed": failed_count,
                "skipped": skipped_count + remaining,
                "remaining": remaining,
                "resumable": True,
                "safe_to_continue": False,
                "individual_results": individual_results,
                "retryable_failures": retryable_failures,
                "non_retryable_failures": non_retryable_failures,
                "quota_estimate": {"units_per_request": 100, "total_units": total_units_consumed},
            }

        all_succeeded = (sent_count == requested and failed_count == 0)
        final_status = "COMPLETED" if all_succeeded else "PARTIAL_FAILURE"
        client.bulk_plan_repo.update_status(plan_id, final_status)

        return {
            "success": all_succeeded,
            "operation": "bulk_reply",
            "mutates_mailbox": sent_count > 0,
            "plan_id": plan_id,
            "status": final_status,
            "requested": requested,
            "successful": sent_count,
            "sent": sent_count,
            "failed": failed_count,
            "skipped": skipped_count,
            "remaining": remaining,
            "resumable": remaining > 0 or failed_count > 0,
            "safe_to_continue": True,
            "individual_results": individual_results,
            "retryable_failures": retryable_failures,
            "non_retryable_failures": non_retryable_failures,
            "quota_estimate": {"units_per_request": 100, "total_units": total_units_consumed},
        }
    except Exception as exc:
        return client.format_tool_error("bulk_reply", exc, mutates_mailbox=True)
