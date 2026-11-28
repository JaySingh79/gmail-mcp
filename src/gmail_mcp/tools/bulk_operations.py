"""Bulk draft generation and bulk draft attachment tools (`gmail_bulk_*`)."""

from __future__ import annotations

import asyncio
import re
import uuid
from typing import Any

from gmail_mcp.core.gmail_client import GmailClientWrapper
from gmail_mcp.core.safety import SafetyError
from gmail_mcp.tools.drafts import resolve_content_or_template
from gmail_mcp.tools.search import DEFAULT_METADATA_HEADERS, gmail_search_messages
from gmail_mcp.tools.sending import (
    _init_bulk_job,
    _load_recipient_records,
    _update_bulk_job,
)

_GREETING_NAME_RE = re.compile(
    r"(?:^|\n)\s*(?:hi|hello|dear|hey)\s+(?:(?:mr|ms|mrs|dr|prof)\.?\s+)?([A-Z][a-zA-Z'.-]{1,40})\b",
    re.IGNORECASE,
)
_SUBJECT_COMPANY_RE = re.compile(
    r"(?:^(?:re|fwd?)\s*:\s*)*.*?\b(?:at|with|for)\s+([A-Z0-9][A-Za-z0-9 .&'()-]{1,60}?)\s*(?:[-|–—]|$)",
    re.IGNORECASE,
)
_BOUNCE_FROM_RE = re.compile(
    r"(?:mailer-daemon|postmaster|mail\s+delivery\s+subsystem|mail-daemon|bounces?)@",
    re.IGNORECASE,
)
_BOUNCE_FROM_DISPLAY_RE = re.compile(
    r"mail\s+delivery\s+subsystem|mailer-daemon",
    re.IGNORECASE,
)
_BOUNCE_SUBJECT_RE = re.compile(
    r"(?:delivery\s+status\s+notification|undelivered\s+mail\s+returned|undeliverable\s*:|mail\s+delivery\s+failed|returned\s+mail|failure\s+notice|delivery\s+failure|message\s+not\s+delivered|message\s+blocked)",
    re.IGNORECASE,
)
_BOUNCE_PHRASE_RE = re.compile(
    r"(?:message\s+blocked|has\s+been\s+blocked|message\s+not\s+delivered|delivery\s+incomplete|"
    r"there\s+was\s+a\s+(?:temporary\s+)?problem\s+delivering\s+your\s+message|"
    r"address\s+not\s+found|your\s+message\s+(?:wasn't|was\s+not|couldn't|could\s+not)\s+delivered|"
    r"recipient\s+address\s+rejected|user\s+unknown|no\s+such\s+user|mailbox\s+unavailable|"
    r"does\s+not\s+exist|the\s+email\s+account\s+that\s+you\s+tried\s+to\s+reach|"
    r"the\s+outbound\s+connection\s+was\s+established|the\s+response\s+from\s+the\s+remote\s+server\s+was|"
    r"remote\s+server\s+returned|diagnostic-code:|action:\s*failed|"
    r"\b(?:550|554|421|450|451|452)[\s-]5?\.\d+\.\d+|"
    r"protection\.outlook\.com|outlook\s+(?:server|delivery)\s+(?:error|issue)|smtp\s+error)",
    re.IGNORECASE,
)
_BOUNCE_METADATA_HEADERS = [
    "From",
    "Subject",
    "Auto-Submitted",
    "X-Failed-Recipients",
    "Content-Type",
]


def _is_bounced_message(msg_resp: dict[str, Any]) -> str | None:
    """Return a short bounce reason if a raw Gmail message indicates a bounce/DSN, else None."""
    payload = msg_resp.get("payload") or {}
    headers_list = payload.get("headers") or []
    headers = {
        str(h.get("name", "")).lower(): str(h.get("value", ""))
        for h in headers_list
        if h.get("name")
    }
    from_val = headers.get("from", "")
    subj_val = headers.get("subject", "")
    content_type = headers.get("content-type", "")
    snippet = str(msg_resp.get("snippet") or "")

    if headers.get("x-failed-recipients"):
        return "X-Failed-Recipients header present"
    if "delivery-status" in content_type.lower() or "multipart/report" in content_type.lower():
        return "multipart/report delivery-status"
    if _BOUNCE_FROM_RE.search(from_val) or _BOUNCE_FROM_DISPLAY_RE.search(from_val):
        return f"bounce sender ({from_val.strip()[:60]})"
    if _BOUNCE_SUBJECT_RE.search(subj_val):
        return f"bounce subject ({subj_val.strip()[:60]})"
    m_phrase = _BOUNCE_PHRASE_RE.search(snippet)
    if m_phrase:
        return f"bounce phrase ({m_phrase.group(0)[:50]})"
    return None


async def _check_thread_for_bounce(
    client: GmailClientWrapper,
    thread_id: str,
) -> tuple[str | None, int]:
    """Inspect all messages in `thread_id` for bounce/DSN indicators."""
    try:
        th_resp, t_meta = await client.execute_api(
            "threads.get",
            {
                "id": thread_id,
                "format": "metadata",
                "metadataHeaders": _BOUNCE_METADATA_HEADERS,
            },
            tool_name="gmail_bulk_create_drafts",
            op_class="read",
            fmt="metadata",
        )
        units = int(t_meta.get("quota_estimate", 40))
    except Exception:
        return None, 0

    for msg in th_resp.get("messages") or []:
        reason = _is_bounced_message(msg)
        if reason:
            return reason, units
    return None, units


def _extract_recipient_name_from_body(body_text: str | None, snippet: str | None = None) -> str | None:
    """Extract first/recipient name from an email greeting (e.g. 'Hello Susmit,')."""
    for text in (body_text, snippet):
        if not text:
            continue
        m = _GREETING_NAME_RE.search(text)
        if m:
            candidate = m.group(1).strip(".,;:")
            if candidate.lower() not in {"there", "all", "team", "everyone", "hiring", "sir", "madam"}:
                return candidate
    return None


def _extract_company_from_subject(subject: str | None) -> str | None:
    """Extract company name from an email subject (e.g. 'Application ... at OneCard')."""
    if not subject:
        return None
    m = _SUBJECT_COMPANY_RE.search(subject.strip())
    if m:
        return m.group(1).strip(" .,;:-")
    return None


async def _auto_populate_reply_context(
    client: GmailClientWrapper,
    rec_dict: dict[str, Any],
    vars_map: dict[str, Any],
) -> int:
    """Fetch parent message when in_reply_to/message_id/thread_id is given and auto-fill reply metadata + vars."""
    msg_id = rec_dict.get("message_id")
    in_reply_to = rec_dict.get("in_reply_to")
    thread_id = rec_dict.get("thread_id")
    if not (msg_id or in_reply_to or thread_id):
        return 0

    needs_meta = (
        not (rec_dict.get("email") or rec_dict.get("to"))
        or not thread_id
        or not in_reply_to
        or not rec_dict.get("references")
        or rec_dict.get("subject") is None
        or "recipient_name" not in vars_map
        or "company" not in vars_map
    )
    if not needs_meta:
        return 0

    units_used = 0
    msg_resp: dict[str, Any] | None = None

    if msg_id:
        msg_resp, m_meta = await client.execute_api(
            "messages.get",
            {
                "id": msg_id,
                "format": "metadata",
                "metadataHeaders": DEFAULT_METADATA_HEADERS,
            },
            tool_name="gmail_bulk_create_drafts",
            op_class="read",
            fmt="metadata",
        )
        units_used += int(m_meta.get("quota_estimate", 20))
    elif in_reply_to:
        raw_ref = str(in_reply_to).strip()
        if "@" in raw_ref or raw_ref.startswith("<"):
            clean_id = raw_ref.strip("<>")
            list_resp, l_meta = await client.execute_api(
                "messages.list",
                {"q": f"rfc822msgid:{clean_id}", "maxResults": 1},
                tool_name="gmail_bulk_create_drafts",
                op_class="read",
            )
            units_used += int(l_meta.get("quota_estimate", 5))
            msgs = list_resp.get("messages") or []
            if msgs:
                msg_resp, m_meta = await client.execute_api(
                    "messages.get",
                    {
                        "id": msgs[0]["id"],
                        "format": "metadata",
                        "metadataHeaders": DEFAULT_METADATA_HEADERS,
                    },
                    tool_name="gmail_bulk_create_drafts",
                    op_class="read",
                    fmt="metadata",
                )
                units_used += int(m_meta.get("quota_estimate", 20))
        else:
            msg_resp, m_meta = await client.execute_api(
                "messages.get",
                {
                    "id": raw_ref,
                    "format": "metadata",
                    "metadataHeaders": DEFAULT_METADATA_HEADERS,
                },
                tool_name="gmail_bulk_create_drafts",
                op_class="read",
                fmt="metadata",
            )
            units_used += int(m_meta.get("quota_estimate", 20))
    elif thread_id:
        th_resp, t_meta = await client.execute_api(
            "threads.get",
            {
                "id": thread_id,
                "format": "metadata",
                "metadataHeaders": DEFAULT_METADATA_HEADERS,
            },
            tool_name="gmail_bulk_create_drafts",
            op_class="read",
            fmt="metadata",
        )
        units_used += int(t_meta.get("quota_estimate", 40))
        msgs = th_resp.get("messages") or []
        if msgs:
            msg_resp = msgs[-1]

    if not msg_resp:
        return units_used

    parsed = client.mime_builder.parse_gmail_payload(msg_resp)
    headers = parsed.get("headers") or {}
    reply_hdrs = client.mime_builder.build_reply_headers(headers)

    if not rec_dict.get("thread_id") and parsed.get("thread_id"):
        rec_dict["thread_id"] = parsed["thread_id"]
    if reply_hdrs.get("in_reply_to"):
        if not rec_dict.get("in_reply_to") or ("@" not in str(rec_dict["in_reply_to"])):
            rec_dict["in_reply_to"] = reply_hdrs["in_reply_to"]
    if not rec_dict.get("references") and reply_hdrs.get("references"):
        rec_dict["references"] = reply_hdrs["references"]
    if rec_dict.get("subject") is None and reply_hdrs.get("subject"):
        rec_dict["subject"] = reply_hdrs["subject"]

    if not (rec_dict.get("email") or rec_dict.get("to")):
        labels = set(parsed.get("label_ids") or [])
        if "SENT" in labels and parsed.get("to"):
            rec_dict["to"] = parsed["to"]
        elif reply_hdrs.get("reply_to_address"):
            rec_dict["to"] = reply_hdrs["reply_to_address"]

    if "recipient_name" not in vars_map:
        extracted_name = _extract_recipient_name_from_body(
            parsed.get("body_text"), parsed.get("snippet")
        )
        if not extracted_name and not parsed.get("body_text") and parsed.get("id"):
            full_resp, f_meta = await client.execute_api(
                "messages.get",
                {"id": parsed["id"], "format": "full"},
                tool_name="gmail_bulk_create_drafts",
                op_class="read",
                fmt="full",
            )
            units_used += int(f_meta.get("quota_estimate", 20))
            full_parsed = client.mime_builder.parse_gmail_payload(full_resp)
            extracted_name = _extract_recipient_name_from_body(
                full_parsed.get("body_text"), full_parsed.get("snippet")
            )
        if extracted_name:
            vars_map["recipient_name"] = extracted_name

    if "company" not in vars_map:
        orig_subj = parsed.get("subject") or headers.get("Subject") or rec_dict.get("subject")
        extracted_company = _extract_company_from_subject(orig_subj)
        if extracted_company:
            vars_map["company"] = extracted_company

    return units_used


async def gmail_bulk_create_drafts(
    client: GmailClientWrapper,
    recipients: list[dict[str, Any] | str] | None = None,
    *,
    recipient_source: str | None = None,
    field_mapping: dict[str, str] | None = None,
    attachment_pattern: str | None = None,
    query: str | None = None,
    max_results: int = 50,
    template_id: str | None = None,
    subject: str | None = None,
    body_text: str | None = None,
    body_html: str | None = None,
    attachments: list[str] | None = None,
    compact: bool = True,
    concurrency: int = 10,
    run_async: bool = False,
    skip_bounced: bool = True,
) -> dict[str, Any]:
    """
    Generate 1 to hundreds of drafts concurrently in parallel processing mode from templates or recipient records.
    Supports per-recipient subjects, bodies, personalization variables, shared attachments, individual attachments,
    `query` batch reply-draft creation, `run_async=True` background execution, and configurable parallel worker concurrency.
    Also accepts `recipient_source` (path to scraped JSON/CSV), `field_mapping`, and `attachment_pattern`.
    """
    try:
        search_units = 0
        if query and recipients is None and recipient_source is None:
            search_res = await gmail_search_messages(
                client,
                query=query,
                max_results=max_results,
                auto_paginate=True,
                format="metadata",
            )
            if not search_res.get("success"):
                return search_res
            search_units = int((search_res.get("quota_estimate") or {}).get("total_units", 0))
            raw_recipients = []
            for m in search_res.get("results") or []:
                headers = m.get("headers") or {}
                reply_hdrs = client.mime_builder.build_reply_headers(headers)
                labels = set(m.get("label_ids") or [])
                to_addr = m.get("to") if ("SENT" in labels and m.get("to")) else reply_hdrs.get("reply_to_address")
                rec_entry: dict[str, Any] = {
                    "to": to_addr,
                    "thread_id": m.get("thread_id"),
                    "in_reply_to": reply_hdrs.get("in_reply_to"),
                    "references": reply_hdrs.get("references"),
                    "subject": reply_hdrs.get("subject"),
                }
                extracted_name = _extract_recipient_name_from_body(None, m.get("snippet"))
                if extracted_name:
                    rec_entry["recipient_name"] = extracted_name
                extracted_company = _extract_company_from_subject(m.get("subject") or headers.get("Subject"))
                if extracted_company:
                    rec_entry["company"] = extracted_company
                raw_recipients.append(rec_entry)
            if not raw_recipients:
                raise SafetyError("EMPTY_SEARCH_RESULTS", f"No messages matched query: {query}")
        else:
            raw_recipients = _load_recipient_records(
                client=client,
                recipients=recipients,
                recipient_source=recipient_source,
                field_mapping=field_mapping,
                attachment_pattern=attachment_pattern,
            )

        shared_attachments = list(attachments or [])
        step = max(1, min(int(concurrency), 25))
        sem = asyncio.Semaphore(step)
        lock = asyncio.Lock()

        total_units = search_units
        drafts_out: list[dict[str, Any]] = []
        failures_out: list[dict[str, Any]] = []
        skipped_out: list[dict[str, Any]] = []
        thread_bounce_cache: dict[str, str | None] = {}
        thread_bounce_locks: dict[str, asyncio.Lock] = {}
        safe_to_continue = True
        requested_count = len(raw_recipients)
        job_id = f"job_create_drafts_{uuid.uuid4().hex[:12]}"
        _init_bulk_job(job_id, operation="bulk_create_drafts", total=requested_count)

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
            "message_id",
            "in_reply_to",
            "references",
        }

        async def _create_one(idx: int, item: Any) -> None:
            nonlocal safe_to_continue, total_units
            if not safe_to_continue:
                return

            if isinstance(item, str):
                rec_dict: dict[str, Any] = {"email": item}
            elif isinstance(item, dict):
                rec_dict = dict(item)
            else:
                async with lock:
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
                return

            to_addr = rec_dict.get("email") or rec_dict.get("to")
            cc_addr = rec_dict.get("cc")
            bcc_addr = rec_dict.get("bcc")
            item_tpl = rec_dict.get("template_id") or template_id
            item_subj = rec_dict.get("subject") if rec_dict.get("subject") is not None else subject
            item_text = rec_dict.get("body_text") if rec_dict.get("body_text") is not None else body_text
            item_html = rec_dict.get("body_html") if rec_dict.get("body_html") is not None else body_html
            item_atts = list(dict.fromkeys(shared_attachments + list(rec_dict.get("attachments") or [])))

            vars_map: dict[str, Any] = {
                k: v for k, v in rec_dict.items() if k not in reserved_keys
            }
            if isinstance(rec_dict.get("variables"), dict):
                vars_map.update(rec_dict["variables"])

            async with sem:
                if not safe_to_continue:
                    return
                try:
                    is_reply = bool(
                        rec_dict.get("thread_id")
                        or rec_dict.get("in_reply_to")
                        or rec_dict.get("message_id")
                    )

                    async def _is_thread_bounced(tid: str) -> str | None:
                        nonlocal total_units
                        async with lock:
                            tlock = thread_bounce_locks.setdefault(tid, asyncio.Lock())
                        async with tlock:
                            if tid not in thread_bounce_cache:
                                b_reason, b_units = await _check_thread_for_bounce(client, tid)
                                thread_bounce_cache[tid] = b_reason
                                async with lock:
                                    total_units += b_units
                            return thread_bounce_cache[tid]

                    # Fast-path: if thread_id is already known, check bounce before fetching full body
                    if skip_bounced and is_reply and rec_dict.get("thread_id"):
                        tid = str(rec_dict["thread_id"])
                        bounce_reason = await _is_thread_bounced(tid)
                        if bounce_reason:
                            to_preview = rec_dict.get("email") or rec_dict.get("to") or ""
                            async with lock:
                                skipped_out.append(
                                    {
                                        "index": idx,
                                        "recipient": str(to_preview),
                                        "thread_id": tid,
                                        "status": "skipped",
                                        "reason": "THREAD_BOUNCED",
                                        "message": f"Skipped reply draft: bounce detected in thread ({bounce_reason})",
                                    }
                                )
                            return

                    had_thread_id = bool(rec_dict.get("thread_id"))
                    auto_units = await _auto_populate_reply_context(client, rec_dict, vars_map)
                    total_units += auto_units

                    if skip_bounced and is_reply and not had_thread_id and rec_dict.get("thread_id"):
                        tid = str(rec_dict["thread_id"])
                        bounce_reason = await _is_thread_bounced(tid)
                        if bounce_reason:
                            to_preview = rec_dict.get("email") or rec_dict.get("to") or ""
                            async with lock:
                                skipped_out.append(
                                    {
                                        "index": idx,
                                        "recipient": str(to_preview),
                                        "thread_id": tid,
                                        "status": "skipped",
                                        "reason": "THREAD_BOUNCED",
                                        "message": f"Skipped reply draft: bounce detected in thread ({bounce_reason})",
                                    }
                                )
                            return

                    to_addr = rec_dict.get("email") or rec_dict.get("to")
                    item_subj = rec_dict.get("subject") if rec_dict.get("subject") is not None else subject

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
                        in_reply_to=rec_dict.get("in_reply_to"),
                        references=rec_dict.get("references"),
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
                    units_consumed = int(meta.get("quota_estimate", 10))
                    item_draft = {
                        "index": idx,
                        "draft_id": resp.get("id"),
                        "message_id": (resp.get("message") or {}).get("id"),
                        "recipient": ", ".join(built["recipients"]["to"]),
                        "subject": resolved["subject"],
                        "attachment_count": len(built["attachments"]),
                        "status": "created",
                    }
                    async with lock:
                        total_units += units_consumed
                        drafts_out.append(item_draft)
                except Exception as item_exc:
                    err = client.format_tool_error("gmail_bulk_create_drafts", item_exc, mutates_mailbox=True)
                    item_failure = {
                        "index": idx,
                        "recipient": str(to_addr or ""),
                        "status": "failed",
                        "error_code": err["error_code"],
                        "message": err["message"],
                        "retryable": err["retryable"],
                    }
                    async with lock:
                        failures_out.append(item_failure)
                        if err["error_code"] in (
                            "QUOTA_EXHAUSTED",
                            "USER_RATE_LIMIT_HEADROOM_EXCEEDED",
                            "PROJECT_RATE_LIMIT_HEADROOM_EXCEEDED",
                            "INSUFFICIENT_SCOPE",
                            "AUTH_REAUTHORIZATION_REQUIRED",
                        ):
                            safe_to_continue = False

        async def _run_all() -> None:
            await asyncio.gather(
                *[_create_one(idx, item) for idx, item in enumerate(raw_recipients)],
                return_exceptions=False,
            )
            _update_bulk_job(
                job_id,
                status="COMPLETED" if len(failures_out) == 0 else "PARTIAL_FAILURE",
                processed=len(drafts_out) + len(failures_out),
                sent=len(drafts_out),
                failed=len(failures_out),
                errors=failures_out[:20],
            )

        if run_async:
            asyncio.create_task(_run_all())
            return {
                "success": True,
                "operation": "bulk_create_drafts",
                "mutates_mailbox": True,
                "status": "RUNNING",
                "job_id": job_id,
                "total": requested_count,
                "concurrency_used": step,
                "message": "Bulk draft creation started in background. Monitor via gmail_get_bulk_status.",
            }

        await _run_all()

        drafts_out.sort(key=lambda d: d["index"])
        failures_out.sort(key=lambda f: f["index"])
        skipped_out.sort(key=lambda s: s["index"])

        created_count = len(drafts_out)
        failed_count = len(failures_out)
        skipped_count = len(skipped_out) + max(0, requested_count - created_count - failed_count - len(skipped_out))
        draft_ids = [d["draft_id"] for d in drafts_out if d.get("draft_id")]

        if compact:
            return {
                "success": (failed_count == 0 and (created_count + len(skipped_out)) == requested_count),
                "operation": "bulk_create_drafts",
                "mutates_mailbox": True,
                "job_id": job_id,
                "requested": requested_count,
                "successful": created_count,
                "created": created_count,
                "failed": failed_count,
                "skipped": skipped_count,
                "concurrency_used": step,
                "draft_ids": draft_ids,
                "drafts": [{"draft_id": d["draft_id"], "recipient": d.get("recipient", "")} for d in drafts_out],
                "sample_drafts": drafts_out[:3],
                "sample_failures": failures_out[:5],
                "sample_skipped": skipped_out[:5],
                "safe_to_continue": safe_to_continue,
                "compact": True,
                "quota_estimate": {
                    "units_per_request": 10,
                    "total_units": total_units,
                },
            }

        return {
            "success": (failed_count == 0 and (created_count + len(skipped_out)) == requested_count),
            "operation": "bulk_create_drafts",
            "mutates_mailbox": True,
            "requested": requested_count,
            "successful": created_count,
            "created": created_count,
            "failed": failed_count,
            "skipped": skipped_count,
            "concurrency_used": step,
            "draft_ids": draft_ids,
            "drafts": drafts_out,
            "skipped_items": skipped_out,
            "individual_results": sorted(drafts_out + failures_out + skipped_out, key=lambda r: r["index"]),
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
    concurrency: int = 10,
) -> dict[str, Any]:
    """
    Attach shared and/or draft-specific files to multiple existing drafts concurrently (e.g. 1 PDF to 100 drafts).
    Implemented via parallel `drafts.get(format='raw')`, MIME reconstruction preserving existing content/headers/attachments,
    and individual `drafts.update` calls bounded by configurable concurrency.
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

        # Pre-validate all shared attachment files once before iterating over drafts
        for att in shared_atts:
            client.safety.validate_attachment_file(att)
        for d_atts in per_draft_map.values():
            for att in d_atts:
                client.safety.validate_attachment_file(att)

        step = max(1, min(int(concurrency), 25))
        sem = asyncio.Semaphore(step)
        lock = asyncio.Lock()

        total_units = 0
        updated_drafts: list[dict[str, Any]] = []
        failed_drafts: list[dict[str, Any]] = []
        safe_to_continue = True

        async def _attach_one(idx: int, did: str) -> None:
            nonlocal safe_to_continue, total_units
            if not safe_to_continue:
                return

            extra_atts = list(dict.fromkeys(shared_atts + list(per_draft_map.get(did) or [])))
            if not extra_atts:
                return

            async with sem:
                if not safe_to_continue:
                    return
                try:
                    get_resp, g_meta = await client.execute_api(
                        "drafts.get",
                        {"id": did, "format": "raw"},
                        tool_name="gmail_bulk_attach_to_drafts",
                        op_class="read",
                        fmt="raw",
                    )
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
                    units_consumed = int(g_meta.get("quota_estimate", 60)) + int(u_meta.get("quota_estimate", 15))
                    item_updated = {
                        "index": idx,
                        "draft_id": upd_resp.get("id") or did,
                        "status": "updated",
                        "total_attachments": len(reconstructed["attachments"]),
                        "attachments": [a["filename"] for a in reconstructed["attachments"]],
                    }
                    async with lock:
                        total_units += units_consumed
                        updated_drafts.append(item_updated)
                except Exception as item_exc:
                    err = client.format_tool_error("gmail_bulk_attach_to_drafts", item_exc, mutates_mailbox=True)
                    item_failed = {
                        "index": idx,
                        "draft_id": did,
                        "status": "failed",
                        "error_code": err["error_code"],
                        "message": err["message"],
                        "retryable": err["retryable"],
                    }
                    async with lock:
                        failed_drafts.append(item_failed)
                        if err["error_code"] in (
                            "QUOTA_EXHAUSTED",
                            "USER_RATE_LIMIT_HEADROOM_EXCEEDED",
                            "PROJECT_RATE_LIMIT_HEADROOM_EXCEEDED",
                        ):
                            safe_to_continue = False

        await asyncio.gather(
            *[_attach_one(idx, did) for idx, did in enumerate(draft_ids)],
            return_exceptions=False,
        )

        updated_drafts.sort(key=lambda d: d["index"])
        failed_drafts.sort(key=lambda f: f["index"])

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
            "concurrency_used": step,
            "drafts": updated_drafts,
            "updated_drafts": updated_drafts,
            "failed_drafts": failed_drafts,
            "individual_results": sorted(updated_drafts + failed_drafts, key=lambda r: r["index"]),
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


async def gmail_bulk_delete_drafts(
    client: GmailClientWrapper,
    *,
    draft_ids: list[str] | None = None,
    query: str | None = None,
    filter_reply_only: bool = False,
    max_delete: int = 50,
    concurrency: int = 10,
    dry_run: bool = False,
    run_async: bool = False,
) -> dict[str, Any]:
    """Delete multiple email drafts concurrently in parallel batch.

    Supports deleting drafts specified by ID list or discovered via Gmail query/filter.

    Args:
        client: Gmail client wrapper.
        draft_ids: Explicit list of draft IDs to permanently delete.
        query: Optional Gmail search query to locate drafts (e.g. 'subject:Application').
        filter_reply_only: If True, inspects candidate drafts and only deletes those that are replies
            (having In-Reply-To/References headers or subject starting with Re:/Following up:).
        max_delete: Maximum number of drafts to delete (default: 50, max: 500).
        concurrency: Number of concurrent deletion tasks (default: 10, max: 25).
        dry_run: If True, previews matched target drafts without deleting them.
        run_async: If True, runs the deletion job in background and returns a job_id for polling.
    """
    try:
        step = min(25, max(1, concurrency))
        limit = min(500, max(1, max_delete))
        total_units = 0

        target_ids: list[str] = []

        if draft_ids:
            target_ids = [d.strip() for d in draft_ids if d and d.strip()]
        elif query or filter_reply_only:
            list_params: dict[str, Any] = {
                "maxResults": min(500, max(50, limit * 3 if filter_reply_only else limit)),
            }
            if query and query.strip():
                list_params["q"] = query.strip()

            list_resp, l_meta = await client.execute_api(
                "drafts.list",
                list_params,
                tool_name="gmail_bulk_delete_drafts",
                op_class="read",
            )
            total_units += int(l_meta.get("quota_estimate", 5))
            raw_drafts = list_resp.get("drafts", []) or []
            target_ids = [d["id"] for d in raw_drafts if d.get("id")]
        else:
            raise SafetyError(
                "MISSING_TARGET",
                "Must specify either 'draft_ids', 'query', or set 'filter_reply_only=True' to target drafts for bulk deletion.",
            )

        if filter_reply_only and target_ids:
            sem = asyncio.Semaphore(step)
            verified_reply_ids: list[str] = []
            filter_lock = asyncio.Lock()

            async def _check_draft(did: str):
                nonlocal total_units
                async with sem:
                    try:
                        d_resp, d_meta = await client.execute_api(
                            "drafts.get",
                            {
                                "id": did,
                                "format": "metadata",
                                "metadataHeaders": ["In-Reply-To", "References", "Subject"],
                            },
                            tool_name="gmail_bulk_delete_drafts",
                            op_class="read",
                            fmt="metadata",
                        )
                        async with filter_lock:
                            total_units += int(d_meta.get("quota_estimate", 5))

                        msg = d_resp.get("message") or {}
                        payload = msg.get("payload") or {}
                        headers = {
                            h.get("name", "").lower(): h.get("value", "")
                            for h in (payload.get("headers") or [])
                        }
                        in_reply_to = headers.get("in-reply-to", "").strip()
                        refs = headers.get("references", "").strip()
                        subj = headers.get("subject", "").strip().lower()
                        thread_id = msg.get("threadId")
                        msg_id = msg.get("id")
                        is_thread_reply = bool(thread_id and msg_id and thread_id != msg_id)

                        is_reply = bool(
                            in_reply_to
                            or refs
                            or is_thread_reply
                            or subj.startswith("re:")
                            or subj.startswith("following up")
                            or subj.startswith("follow up")
                            or subj.startswith("follow-up")
                        )
                        if is_reply:
                            async with filter_lock:
                                verified_reply_ids.append(did)
                    except Exception:
                        pass

            await asyncio.gather(*[_check_draft(did) for did in target_ids], return_exceptions=True)
            target_ids = verified_reply_ids

        target_ids = target_ids[:limit]

        if dry_run:
            return {
                "success": True,
                "operation": "bulk_delete_drafts",
                "mutates_mailbox": False,
                "dry_run": True,
                "target_count": len(target_ids),
                "draft_ids": target_ids,
                "message": f"Dry run: {len(target_ids)} drafts matched and would be deleted.",
                "quota_estimate": {
                    "units_per_request": 10,
                    "total_units": total_units,
                },
            }

        if not target_ids:
            return {
                "success": True,
                "operation": "bulk_delete_drafts",
                "mutates_mailbox": False,
                "total_targets": 0,
                "deleted_count": 0,
                "failed_count": 0,
                "deleted_draft_ids": [],
                "failed": [],
                "message": "No matching drafts found to delete.",
                "quota_estimate": {
                    "units_per_request": 10,
                    "total_units": total_units,
                },
            }

        if run_async:
            job_id = f"job_delete_drafts_{uuid.uuid4().hex[:12]}"
            _init_bulk_job(job_id, operation="bulk_delete_drafts", total=len(target_ids))

            async def _bg_delete():
                sem = asyncio.Semaphore(step)
                bg_deleted: list[str] = []
                bg_failed: list[dict[str, Any]] = []
                bg_lock = asyncio.Lock()

                async def _del_item(idx: int, did: str):
                    async with sem:
                        try:
                            await client.execute_api(
                                "drafts.delete",
                                {"id": did},
                                tool_name="gmail_bulk_delete_drafts",
                                op_class="write",
                            )
                            async with bg_lock:
                                bg_deleted.append(did)
                        except Exception as dex:
                            err = client.format_tool_error("gmail_bulk_delete_drafts", dex, mutates_mailbox=True)
                            async with bg_lock:
                                bg_failed.append({"draft_id": did, "error": err["message"]})
                        finally:
                            async with bg_lock:
                                _update_bulk_job(
                                    job_id,
                                    processed=len(bg_deleted) + len(bg_failed),
                                    sent=len(bg_deleted),
                                    failed=len(bg_failed),
                                    errors=bg_failed[:5],
                                )

                await asyncio.gather(*[_del_item(i, did) for i, did in enumerate(target_ids)], return_exceptions=True)
                _update_bulk_job(
                    job_id,
                    status="COMPLETED" if not bg_failed else "COMPLETED_WITH_ERRORS",
                    processed=len(target_ids),
                    sent=len(bg_deleted),
                    failed=len(bg_failed),
                )

            asyncio.create_task(_bg_delete())
            return {
                "success": True,
                "operation": "bulk_delete_drafts",
                "job_id": job_id,
                "status": "RUNNING",
                "total_targets": len(target_ids),
                "message": f"Bulk draft deletion job '{job_id}' started in background for {len(target_ids)} drafts.",
                "quota_estimate": {
                    "units_per_request": 10,
                    "total_units": total_units,
                },
            }

        sem = asyncio.Semaphore(step)
        deleted_ids: list[str] = []
        failed_items: list[dict[str, Any]] = []
        del_lock = asyncio.Lock()

        async def _del_one(idx: int, did: str):
            nonlocal total_units
            async with sem:
                try:
                    _, d_meta = await client.execute_api(
                        "drafts.delete",
                        {"id": did},
                        tool_name="gmail_bulk_delete_drafts",
                        op_class="write",
                    )
                    async with del_lock:
                        total_units += int(d_meta.get("quota_estimate", 10))
                        deleted_ids.append(did)
                except Exception as dex:
                    err = client.format_tool_error("gmail_bulk_delete_drafts", dex, mutates_mailbox=True)
                    async with del_lock:
                        failed_items.append({
                            "index": idx,
                            "draft_id": did,
                            "error_code": err["error_code"],
                            "message": err["message"],
                            "retryable": err.get("retryable", False),
                        })

        await asyncio.gather(*[_del_one(i, did) for i, did in enumerate(target_ids)], return_exceptions=True)

        return {
            "success": len(failed_items) == 0,
            "operation": "bulk_delete_drafts",
            "mutates_mailbox": True,
            "total_targets": len(target_ids),
            "deleted_count": len(deleted_ids),
            "failed_count": len(failed_items),
            "deleted_draft_ids": deleted_ids,
            "failed": failed_items,
            "concurrency_used": step,
            "quota_estimate": {
                "units_per_request": 10,
                "total_units": total_units,
            },
        }
    except Exception as exc:
        return client.format_tool_error("gmail_bulk_delete_drafts", exc, mutates_mailbox=True)


async def gmail_bulk_delete_draft(
    client: GmailClientWrapper,
    **kwargs: Any,
) -> dict[str, Any]:
    """Alias for gmail_bulk_delete_drafts."""
    return await gmail_bulk_delete_drafts(client, **kwargs)

