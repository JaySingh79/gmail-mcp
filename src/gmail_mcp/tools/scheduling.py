"""Persistent SQLite-backed scheduled email sending tools and background worker."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any

from gmail_mcp.core.gmail_client import GmailClientWrapper
from gmail_mcp.core.safety import SafetyError
from gmail_mcp.tools.drafts import resolve_content_or_template


def _parse_scheduled_at(scheduled_at: str) -> tuple[str, float]:
    """Parse an ISO-8601 timestamp into canonical UTC ISO string and POSIX epoch seconds."""
    if not scheduled_at or not scheduled_at.strip():
        raise SafetyError("INVALID_SCHEDULE_TIME", "`scheduled_at` ISO-8601 timestamp is required.")
    raw = scheduled_at.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise SafetyError(
            "INVALID_SCHEDULE_TIME",
            f"Unable to parse `scheduled_at` ('{scheduled_at}') as ISO-8601 datetime.",
        ) from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    utc_dt = dt.astimezone(timezone.utc)
    return utc_dt.isoformat(), utc_dt.timestamp()


async def gmail_schedule_send(
    client: GmailClientWrapper,
    recipient: str | list[str],
    scheduled_at: str,
    *,
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
    """
    Schedule an email for future delivery using the local persistent SQLite scheduler.
    Note: Scheduled delivery is executed by the local MCP server/worker (not a fictional native Gmail API endpoint)
    and requires the server or worker to be running at or after `scheduled_at`.
    """
    try:
        sched_iso, sched_epoch = _parse_scheduled_at(scheduled_at)
        resolved = resolve_content_or_template(
            client,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            template_id=template_id,
            variables=variables,
            attachments=attachments,
        )
        # Validate recipients and MIME build upfront so invalid jobs are never queued
        built = client.mime_builder.build_email_message(
            to=recipient,
            cc=cc,
            bcc=bcc,
            reply_to=reply_to,
            subject=resolved["subject"],
            body_text=resolved["body_text"],
            body_html=resolved["body_html"],
            attachments=resolved["attachments"],
        )
        rec_info = built["recipients"]
        primary_recipient = ", ".join(rec_info["to"])

        idem = idempotency_key or (
            "sched_idem_"
            + hashlib.sha256(
                json.dumps(
                    {
                        "to": rec_info["to"],
                        "scheduled_at": sched_iso,
                        "subject": resolved["subject"],
                        "body_text": resolved["body_text"],
                        "body_html": resolved["body_html"],
                    },
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()[:20]
        )

        job = client.scheduled_job_repo.create_job(
            scheduled_at_iso=sched_iso,
            scheduled_epoch=sched_epoch,
            recipient=primary_recipient,
            idempotency_key=idem,
            template_id=template_id,
            cc=rec_info["cc"],
            bcc=rec_info["bcc"],
            reply_to=reply_to,
            subject=resolved["subject"],
            body_text=resolved["body_text"],
            body_html=resolved["body_html"],
            variables=variables or {},
            attachments=[str(a.get("path") or a["filename"]) for a in built["attachments"]],
            thread_id=thread_id,
        )

        return {
            "success": True,
            "operation": "gmail_schedule_send",
            "mutates_mailbox": False,
            "job_id": job.job_id,
            "idempotency_key": job.idempotency_key,
            "status": job.status,
            "scheduled_at": job.scheduled_at,
            "recipient": job.recipient,
            "subject": job.subject,
            "operational_note": (
                "Scheduled jobs are persisted in SQLite and executed by the local MCP server worker "
                "when operational."
            ),
            "quota_estimate": {"units_per_request": 0, "total_units": 0},
        }
    except Exception as exc:
        return client.format_tool_error("gmail_schedule_send", exc, mutates_mailbox=False)


async def gmail_list_scheduled_sends(
    client: GmailClientWrapper,
    *,
    status: str | None = None,
) -> dict[str, Any]:
    """List scheduled email jobs stored in SQLite (optionally filtered by status)."""
    try:
        jobs = client.scheduled_job_repo.list_jobs(status=status)
        sanitized_jobs = []
        for j in jobs:
            d = j.model_dump()
            # Never expose raw BCC addresses in user-facing summaries
            bcc_list = d.pop("bcc", [])
            d["bcc_count"] = len(bcc_list)
            sanitized_jobs.append(d)

        return {
            "success": True,
            "operation": "gmail_list_scheduled_sends",
            "mutates_mailbox": False,
            "count": len(sanitized_jobs),
            "jobs": sanitized_jobs,
            "quota_estimate": {"units_per_request": 0, "total_units": 0},
        }
    except Exception as exc:
        return client.format_tool_error("gmail_list_scheduled_sends", exc, mutates_mailbox=False)


async def gmail_cancel_scheduled_send(
    client: GmailClientWrapper,
    job_id: str,
) -> dict[str, Any]:
    """Cancel a pending scheduled email job in SQLite."""
    try:
        cancelled, msg = client.scheduled_job_repo.cancel_job(job_id)
        if not cancelled:
            return {
                "success": False,
                "operation": "gmail_cancel_scheduled_send",
                "mutates_mailbox": False,
                "job_id": job_id,
                "error_code": "SCHEDULE_CANCEL_FAILED",
                "message": msg,
                "http_status": 400,
                "retryable": False,
            }
        return {
            "success": True,
            "operation": "gmail_cancel_scheduled_send",
            "mutates_mailbox": False,
            "job_id": job_id,
            "status": "CANCELLED",
            "message": msg,
            "quota_estimate": {"units_per_request": 0, "total_units": 0},
        }
    except Exception as exc:
        return client.format_tool_error("gmail_cancel_scheduled_send", exc, mutates_mailbox=False)


async def gmail_process_due_scheduled_sends(
    client: GmailClientWrapper,
    *,
    current_epoch: float | None = None,
) -> dict[str, Any]:
    """
    Claim and execute all due scheduled jobs from SQLite.
    Recovers pending/interrupted jobs after restart, enforces quota and daily sending limits,
    prevents duplicate sends via `send_ledger`, and records detailed execution status/errors on each job.
    """
    try:
        due_jobs = client.scheduled_job_repo.claim_due_jobs(current_epoch=current_epoch)
        sent_jobs: list[dict[str, Any]] = []
        failed_jobs: list[dict[str, Any]] = []
        total_units = 0

        for job in due_jobs:
            try:
                built = client.mime_builder.build_email_message(
                    to=[x.strip() for x in job.recipient.split(",") if x.strip()],
                    cc=job.cc,
                    bcc=job.bcc,
                    reply_to=job.reply_to,
                    subject=job.subject or "",
                    body_text=job.body_text,
                    body_html=job.body_html,
                    attachments=job.attachments,
                )
                rec_info = built["recipients"]
                resp, meta = await client.send_raw_mime_safely(
                    raw_b64=built["raw"],
                    rfc_message_id=built["rfc_message_id"],
                    recipient_summary=job.recipient,
                    operation_type="scheduled_send",
                    idempotency_key=job.idempotency_key,
                    tool_name="gmail_process_due_scheduled_sends",
                    thread_id=job.thread_id,
                    cc_count=len(rec_info["cc"]),
                    bcc_count=rec_info["bcc_count"],
                    total_recipient_count=rec_info["total_count"],
                    attachment_bytes=built["raw_mime_size_bytes"],
                )
                total_units += int(meta.get("quota_estimate", 100))
                res_payload = {
                    "status": "SENT",
                    "gmail_message_id": resp.get("id"),
                    "thread_id": resp.get("threadId"),
                    "deduplicated": bool(resp.get("deduplicated")),
                    "executed_at": datetime.now(timezone.utc).isoformat(),
                }
                client.scheduled_job_repo.complete_job(
                    job.job_id,
                    status="SENT",
                    gmail_message_id=resp.get("id"),
                    result=res_payload,
                )
                sent_jobs.append({"job_id": job.job_id, "recipient": job.recipient, **res_payload})
            except Exception as job_exc:
                err_info = client.format_tool_error("gmail_process_due_scheduled_sends", job_exc, mutates_mailbox=True)
                fail_payload = {
                    "status": "FAILED",
                    "error_code": err_info["error_code"],
                    "message": err_info["message"],
                    "retryable": err_info["retryable"],
                    "failed_at": datetime.now(timezone.utc).isoformat(),
                }
                client.scheduled_job_repo.complete_job(
                    job.job_id,
                    status="FAILED",
                    result=fail_payload,
                )
                failed_jobs.append({"job_id": job.job_id, "recipient": job.recipient, **fail_payload})

        return {
            "success": len(failed_jobs) == 0,
            "operation": "gmail_process_due_scheduled_sends",
            "mutates_mailbox": len(sent_jobs) > 0,
            "claimed_count": len(due_jobs),
            "sent_count": len(sent_jobs),
            "failed_count": len(failed_jobs),
            "sent_jobs": sent_jobs,
            "failed_jobs": failed_jobs,
            "quota_estimate": {
                "units_per_request": 100 if sent_jobs else 0,
                "total_units": total_units,
            },
        }
    except Exception as exc:
        return client.format_tool_error("gmail_process_due_scheduled_sends", exc, mutates_mailbox=True)


class ScheduledSendWorker:
    """Background asyncio worker that periodically processes due scheduled sends while the MCP server runs."""

    def __init__(self, client: GmailClientWrapper, poll_interval_seconds: float = 15.0) -> None:
        self.client = client
        self.poll_interval_seconds = poll_interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop_event.clear()
            self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                auth = self.client.token_manager.get_auth_status(auto_refresh=False)
                if auth.authenticated or self.client._custom_dispatcher is not None:
                    await gmail_process_due_scheduled_sends(self.client, current_epoch=time.time())
            except Exception:
                pass
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.poll_interval_seconds)
            except asyncio.TimeoutError:
                continue
