"""Data-access repositories backed by SQLite transactions."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from gmail_mcp.storage.database import DatabaseManager
from gmail_mcp.storage.models import (
    BulkPlanRecord,
    ScheduledJobRecord,
    SendLedgerRecord,
    SyncStateRecord,
    TemplateRecord,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TemplateRepository:
    """Persistent template management with multi-version history."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    def create(
        self,
        template_id: str,
        name: str,
        subject: str,
        body_text: str | None = None,
        body_html: str | None = None,
        variables: list[str] | None = None,
        defaults: dict[str, str] | None = None,
        attachments: list[str] | None = None,
    ) -> TemplateRecord:
        now = _now_iso()
        vars_list = variables or []
        defs_dict = defaults or {}
        atts_list = attachments or []
        with self.db.transaction() as conn:
            existing = conn.execute(
                "SELECT template_id FROM templates WHERE template_id = ?", (template_id,)
            ).fetchone()
            if existing:
                raise ValueError(f"Template '{template_id}' already exists. Use update instead.")

            conn.execute(
                """
                INSERT INTO templates (
                    template_id, name, subject, body_text, body_html,
                    variables_json, defaults_json, attachments_json,
                    version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    template_id,
                    name,
                    subject,
                    body_text,
                    body_html,
                    json.dumps(vars_list),
                    json.dumps(defs_dict),
                    json.dumps(atts_list),
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO template_versions (
                    template_id, version, name, subject, body_text, body_html,
                    variables_json, defaults_json, attachments_json, created_at
                ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    template_id,
                    name,
                    subject,
                    body_text,
                    body_html,
                    json.dumps(vars_list),
                    json.dumps(defs_dict),
                    json.dumps(atts_list),
                    now,
                ),
            )
        return self.get(template_id)  # type: ignore[return-value]

    def get(self, template_id: str, version: int | None = None) -> TemplateRecord | None:
        with self.db.transaction() as conn:
            if version is not None:
                row = conn.execute(
                    """
                    SELECT t.template_id, v.name, v.subject, v.body_text, v.body_html,
                           v.variables_json, v.defaults_json, v.attachments_json,
                           v.version, t.created_at, v.created_at AS updated_at
                    FROM template_versions v
                    JOIN templates t ON t.template_id = v.template_id
                    WHERE v.template_id = ? AND v.version = ?
                    """,
                    (template_id, version),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM templates WHERE template_id = ?", (template_id,)
                ).fetchone()
            if not row:
                return None
            return self._row_to_model(row)

    def list_all(self) -> list[TemplateRecord]:
        with self.db.transaction() as conn:
            rows = conn.execute("SELECT * FROM templates ORDER BY updated_at DESC").fetchall()
            return [self._row_to_model(r) for r in rows]

    def update(
        self,
        template_id: str,
        name: str | None = None,
        subject: str | None = None,
        body_text: str | None = None,
        body_html: str | None = None,
        variables: list[str] | None = None,
        defaults: dict[str, str] | None = None,
        attachments: list[str] | None = None,
    ) -> TemplateRecord:
        current = self.get(template_id)
        if not current:
            raise KeyError(f"Template '{template_id}' not found.")

        new_version = current.version + 1
        new_name = name if name is not None else current.name
        new_subject = subject if subject is not None else current.subject
        new_body_text = body_text if body_text is not None else current.body_text
        new_body_html = body_html if body_html is not None else current.body_html
        new_vars = variables if variables is not None else current.variables
        new_defs = defaults if defaults is not None else current.defaults
        new_atts = attachments if attachments is not None else current.attachments
        now = _now_iso()

        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE templates
                SET name = ?, subject = ?, body_text = ?, body_html = ?,
                    variables_json = ?, defaults_json = ?, attachments_json = ?,
                    version = ?, updated_at = ?
                WHERE template_id = ?
                """,
                (
                    new_name,
                    new_subject,
                    new_body_text,
                    new_body_html,
                    json.dumps(new_vars),
                    json.dumps(new_defs),
                    json.dumps(new_atts),
                    new_version,
                    now,
                    template_id,
                ),
            )
            conn.execute(
                """
                INSERT INTO template_versions (
                    template_id, version, name, subject, body_text, body_html,
                    variables_json, defaults_json, attachments_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    template_id,
                    new_version,
                    new_name,
                    new_subject,
                    new_body_text,
                    new_body_html,
                    json.dumps(new_vars),
                    json.dumps(new_defs),
                    json.dumps(new_atts),
                    now,
                ),
            )
        return self.get(template_id)  # type: ignore[return-value]

    def delete(self, template_id: str) -> bool:
        with self.db.transaction() as conn:
            cur = conn.execute("DELETE FROM templates WHERE template_id = ?", (template_id,))
            return cur.rowcount > 0

    @staticmethod
    def _row_to_model(row: Any) -> TemplateRecord:
        return TemplateRecord(
            template_id=row["template_id"],
            name=row["name"],
            subject=row["subject"],
            body_text=row["body_text"],
            body_html=row["body_html"],
            variables=json.loads(row["variables_json"] or "[]"),
            defaults=json.loads(row["defaults_json"] or "{}"),
            attachments=json.loads(row["attachments_json"] or "[]"),
            version=int(row["version"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class SendLedgerRepository:
    """Auditable send history ledger enforcing idempotency and 24-hour send tracking."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    def get_by_idempotency_key(self, idempotency_key: str) -> SendLedgerRecord | None:
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM send_ledger WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            return self._row_to_model(row) if row else None

    def get_by_rfc_message_id(self, rfc_message_id: str) -> SendLedgerRecord | None:
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM send_ledger WHERE rfc_message_id = ?", (rfc_message_id,)
            ).fetchone()
            return self._row_to_model(row) if row else None

    def record_send(
        self,
        idempotency_key: str,
        operation_type: str,
        recipient: str,
        status: str,
        cc_count: int = 0,
        bcc_count: int = 0,
        total_recipient_count: int = 1,
        plan_id: str | None = None,
        thread_id: str | None = None,
        gmail_message_id: str | None = None,
        rfc_message_id: str | None = None,
        quota_cost: int = 100,
        error_code: str | None = None,
        error_message: str | None = None,
        operation_id: str | None = None,
    ) -> SendLedgerRecord:
        now = _now_iso()
        epoch = time.time()
        op_id = operation_id or f"op_{uuid.uuid4().hex[:16]}"
        with self.db.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM send_ledger WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if existing:
                attempts = int(existing["attempt_count"]) + 1
                conn.execute(
                    """
                    UPDATE send_ledger
                    SET status = ?, attempt_count = ?, gmail_message_id = COALESCE(?, gmail_message_id),
                        rfc_message_id = COALESCE(?, rfc_message_id), error_code = ?, error_message = ?,
                        updated_at = ?
                    WHERE idempotency_key = ?
                    """,
                    (
                        status,
                        attempts,
                        gmail_message_id,
                        rfc_message_id,
                        error_code,
                        error_message,
                        now,
                        idempotency_key,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO send_ledger (
                        operation_id, idempotency_key, plan_id, operation_type, recipient,
                        cc_count, bcc_count, total_recipient_count, thread_id,
                        gmail_message_id, rfc_message_id, status, attempt_count,
                        quota_cost, error_code, error_message, created_at, updated_at, epoch_seconds
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        op_id,
                        idempotency_key,
                        plan_id,
                        operation_type,
                        recipient,
                        cc_count,
                        bcc_count,
                        total_recipient_count,
                        thread_id,
                        gmail_message_id,
                        rfc_message_id,
                        status,
                        quota_cost,
                        error_code,
                        error_message,
                        now,
                        now,
                        epoch,
                    ),
                )
        return self.get_by_idempotency_key(idempotency_key)  # type: ignore[return-value]

    def get_24h_sent_stats(self, window_seconds: float = 86400.0) -> dict[str, int]:
        """Count messages and total recipients sent in the rolling 24-hour window."""
        cutoff = time.time() - window_seconds
        with self.db.transaction() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS message_count,
                       COALESCE(SUM(total_recipient_count), 0) AS recipient_count
                FROM send_ledger
                WHERE epoch_seconds >= ? AND status IN ('SENT', 'INDETERMINATE')
                """,
                (cutoff,),
            ).fetchone()
            return {
                "messages_sent_24h": int(row["message_count"] or 0),
                "recipients_sent_24h": int(row["recipient_count"] or 0),
            }

    def list_by_plan_id(self, plan_id: str) -> list[SendLedgerRecord]:
        with self.db.transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM send_ledger WHERE plan_id = ? ORDER BY created_at ASC", (plan_id,)
            ).fetchall()
            return [self._row_to_model(r) for r in rows]

    @staticmethod
    def _row_to_model(row: Any) -> SendLedgerRecord:
        return SendLedgerRecord(
            operation_id=row["operation_id"],
            idempotency_key=row["idempotency_key"],
            plan_id=row["plan_id"],
            operation_type=row["operation_type"],
            recipient=row["recipient"],
            cc_count=int(row["cc_count"]),
            bcc_count=int(row["bcc_count"]),
            total_recipient_count=int(row["total_recipient_count"]),
            thread_id=row["thread_id"],
            gmail_message_id=row["gmail_message_id"],
            rfc_message_id=row["rfc_message_id"],
            status=row["status"],
            attempt_count=int(row["attempt_count"]),
            quota_cost=int(row["quota_cost"]),
            error_code=row["error_code"],
            error_message=row["error_message"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            epoch_seconds=float(row["epoch_seconds"]),
        )


class BulkPlanRepository:
    """Stores and verifies cryptographic integrity for two-phase bulk send/reply plans."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    @staticmethod
    def compute_plan_hash(plan_type: str, items: list[dict[str, Any]]) -> str:
        canonical = json.dumps({"plan_type": plan_type, "items": items}, sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def create_plan(
        self,
        plan_type: str,
        items: list[dict[str, Any]],
        confirmation_phrase: str,
        total_recipient_count: int,
        estimated_quota_units: int,
        ttl_minutes: int = 120,
        plan_id: str | None = None,
    ) -> BulkPlanRecord:
        pid = plan_id or f"plan_{uuid.uuid4().hex[:12]}"
        plan_hash = self.compute_plan_hash(plan_type, items)
        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.isoformat()
        exp_iso = (now_dt + timedelta(minutes=ttl_minutes)).isoformat()

        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO bulk_plans (
                    plan_id, plan_type, plan_hash, confirmation_phrase,
                    requested_count, total_recipient_count, estimated_quota_units,
                    items_json, status, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PREPARED', ?, ?)
                """,
                (
                    pid,
                    plan_type,
                    plan_hash,
                    confirmation_phrase,
                    len(items),
                    total_recipient_count,
                    estimated_quota_units,
                    json.dumps(items),
                    now_iso,
                    exp_iso,
                ),
            )
        return self.get(pid)  # type: ignore[return-value]

    def get(self, plan_id: str) -> BulkPlanRecord | None:
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM bulk_plans WHERE plan_id = ?", (plan_id,)
            ).fetchone()
            return self._row_to_model(row) if row else None

    def update_status(self, plan_id: str, status: str) -> None:
        now = _now_iso()
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE bulk_plans SET status = ?, executed_at = ? WHERE plan_id = ?",
                (status, now, plan_id),
            )

    def verify_integrity(self, plan: BulkPlanRecord) -> bool:
        expected = self.compute_plan_hash(plan.plan_type, plan.items)
        return expected == plan.plan_hash

    @staticmethod
    def _row_to_model(row: Any) -> BulkPlanRecord:
        return BulkPlanRecord(
            plan_id=row["plan_id"],
            plan_type=row["plan_type"],
            plan_hash=row["plan_hash"],
            confirmation_phrase=row["confirmation_phrase"],
            requested_count=int(row["requested_count"]),
            total_recipient_count=int(row["total_recipient_count"]),
            estimated_quota_units=int(row["estimated_quota_units"]),
            items=json.loads(row["items_json"] or "[]"),
            status=row["status"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            executed_at=row["executed_at"],
        )


class ScheduledJobRepository:
    """Persistent scheduled sending queue surviving server restarts."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    def create_job(
        self,
        scheduled_at_iso: str,
        scheduled_epoch: float,
        recipient: str,
        idempotency_key: str,
        template_id: str | None = None,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        reply_to: str | None = None,
        subject: str | None = None,
        body_text: str | None = None,
        body_html: str | None = None,
        variables: dict[str, Any] | None = None,
        attachments: list[str] | None = None,
        thread_id: str | None = None,
        job_id: str | None = None,
    ) -> ScheduledJobRecord:
        jid = job_id or f"sched_{uuid.uuid4().hex[:12]}"
        now = _now_iso()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO scheduled_jobs (
                    job_id, idempotency_key, scheduled_at, scheduled_epoch,
                    template_id, recipient, cc_json, bcc_json, reply_to,
                    subject, body_text, body_html, variables_json, attachments_json,
                    thread_id, status, attempt_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', 0, ?, ?)
                """,
                (
                    jid,
                    idempotency_key,
                    scheduled_at_iso,
                    scheduled_epoch,
                    template_id,
                    recipient,
                    json.dumps(cc or []),
                    json.dumps(bcc or []),
                    reply_to,
                    subject,
                    body_text,
                    body_html,
                    json.dumps(variables or {}),
                    json.dumps(attachments or []),
                    thread_id,
                    now,
                    now,
                ),
            )
        return self.get(jid)  # type: ignore[return-value]

    def get(self, job_id: str) -> ScheduledJobRecord | None:
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM scheduled_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            return self._row_to_model(row) if row else None

    def list_jobs(self, status: str | None = None) -> list[ScheduledJobRecord]:
        with self.db.transaction() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM scheduled_jobs WHERE status = ? ORDER BY scheduled_epoch ASC",
                    (status.upper(),),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM scheduled_jobs ORDER BY scheduled_epoch ASC"
                ).fetchall()
            return [self._row_to_model(r) for r in rows]

    def claim_due_jobs(self, current_epoch: float | None = None) -> list[ScheduledJobRecord]:
        """Atomically transition due PENDING jobs (and stale PROCESSING jobs from prior crashes) to PROCESSING."""
        now_epoch = current_epoch if current_epoch is not None else time.time()
        now_iso = _now_iso()
        claimed_ids: list[str] = []
        with self.db.transaction() as conn:
            # Recover any jobs stuck in PROCESSING from an abnormal process termination
            rows = conn.execute(
                """
                SELECT job_id FROM scheduled_jobs
                WHERE (status = 'PENDING' AND scheduled_epoch <= ?)
                   OR (status = 'PROCESSING' AND scheduled_epoch <= ? AND attempt_count < 3)
                ORDER BY scheduled_epoch ASC
                """,
                (now_epoch, now_epoch),
            ).fetchall()
            for r in rows:
                jid = r["job_id"]
                cur = conn.execute(
                    """
                    UPDATE scheduled_jobs
                    SET status = 'PROCESSING', attempt_count = attempt_count + 1, updated_at = ?
                    WHERE job_id = ? AND status IN ('PENDING', 'PROCESSING')
                    """,
                    (now_iso, jid),
                )
                if cur.rowcount > 0:
                    claimed_ids.append(jid)
        return [self.get(jid) for jid in claimed_ids if self.get(jid) is not None]  # type: ignore[misc]

    def complete_job(
        self,
        job_id: str,
        status: str,
        gmail_message_id: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> ScheduledJobRecord | None:
        now = _now_iso()
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE scheduled_jobs
                SET status = ?, gmail_message_id = COALESCE(?, gmail_message_id),
                    last_result_json = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (status, gmail_message_id, json.dumps(result or {}), now, job_id),
            )
        return self.get(job_id)

    def cancel_job(self, job_id: str) -> tuple[bool, str]:
        now = _now_iso()
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT status FROM scheduled_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if not row:
                return False, f"Scheduled job '{job_id}' not found."
            if row["status"] != "PENDING":
                return False, f"Cannot cancel job '{job_id}' in status '{row['status']}'."
            conn.execute(
                "UPDATE scheduled_jobs SET status = 'CANCELLED', updated_at = ? WHERE job_id = ?",
                (now, job_id),
            )
            return True, f"Scheduled job '{job_id}' cancelled."

    @staticmethod
    def _row_to_model(row: Any) -> ScheduledJobRecord:
        return ScheduledJobRecord(
            job_id=row["job_id"],
            idempotency_key=row["idempotency_key"],
            scheduled_at=row["scheduled_at"],
            scheduled_epoch=float(row["scheduled_epoch"]),
            template_id=row["template_id"],
            recipient=row["recipient"],
            cc=json.loads(row["cc_json"] or "[]"),
            bcc=json.loads(row["bcc_json"] or "[]"),
            reply_to=row["reply_to"],
            subject=row["subject"],
            body_text=row["body_text"],
            body_html=row["body_html"],
            variables=json.loads(row["variables_json"] or "{}"),
            attachments=json.loads(row["attachments_json"] or "[]"),
            thread_id=row["thread_id"],
            status=row["status"],
            attempt_count=int(row["attempt_count"]),
            gmail_message_id=row["gmail_message_id"],
            last_result=json.loads(row["last_result_json"]) if row["last_result_json"] else None,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class SyncStateRepository:
    """Stores Gmail history synchronization cursor and deduplicates Pub/Sub notifications."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    def get_state(self, account_key: str = "default") -> SyncStateRecord | None:
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM sync_state WHERE account_key = ?", (account_key,)
            ).fetchone()
            if not row:
                return None
            return SyncStateRecord(
                account_key=row["account_key"],
                email_address=row["email_address"],
                history_id=row["history_id"],
                watch_expiration=row["watch_expiration"],
                topic_name=row["topic_name"],
                last_sync_at=row["last_sync_at"],
                updated_at=row["updated_at"],
            )

    def upsert_state(
        self,
        history_id: str,
        account_key: str = "default",
        email_address: str | None = None,
        watch_expiration: str | None = None,
        topic_name: str | None = None,
    ) -> SyncStateRecord:
        now = _now_iso()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO sync_state (
                    account_key, email_address, history_id, watch_expiration,
                    topic_name, last_sync_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_key) DO UPDATE SET
                    email_address = COALESCE(excluded.email_address, sync_state.email_address),
                    history_id = excluded.history_id,
                    watch_expiration = COALESCE(excluded.watch_expiration, sync_state.watch_expiration),
                    topic_name = COALESCE(excluded.topic_name, sync_state.topic_name),
                    last_sync_at = excluded.last_sync_at,
                    updated_at = excluded.updated_at
                """,
                (account_key, email_address, str(history_id), watch_expiration, topic_name, now, now),
            )
        return self.get_state(account_key)  # type: ignore[return-value]

    def is_notification_processed(self, notification_id: str) -> bool:
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT 1 FROM processed_notifications WHERE notification_id = ?",
                (notification_id,),
            ).fetchone()
            return row is not None

    def mark_notification_processed(self, notification_id: str, history_id: str) -> bool:
        """Record notification_id; returns False if it was already processed."""
        now = _now_iso()
        epoch = time.time()
        with self.db.transaction() as conn:
            existing = conn.execute(
                "SELECT 1 FROM processed_notifications WHERE notification_id = ?",
                (notification_id,),
            ).fetchone()
            if existing:
                return False
            conn.execute(
                """
                INSERT INTO processed_notifications (notification_id, history_id, processed_at, epoch_seconds)
                VALUES (?, ?, ?, ?)
                """,
                (notification_id, str(history_id), now, epoch),
            )
            return True


class QuotaWindowRepository:
    """Shared SQLite quota event log supporting multi-process rate-limit coordination."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

    def record_usage(self, method_name: str, units: int, bucket_scope: str = "user") -> None:
        now = time.time()
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO quota_events (bucket_scope, method_name, units, epoch_seconds) VALUES (?, ?, ?, ?)",
                (bucket_scope, method_name, int(units), now),
            )
            # Prune events older than 24 hours occasionally
            if int(now) % 50 == 0:
                conn.execute("DELETE FROM quota_events WHERE epoch_seconds < ?", (now - 86400.0,))

    def get_rolling_usage(self, window_seconds: float = 60.0, bucket_scope: str = "user") -> int:
        cutoff = time.time() - window_seconds
        with self.db.transaction() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(units), 0) AS total_units
                FROM quota_events
                WHERE bucket_scope = ? AND epoch_seconds >= ?
                """,
                (bucket_scope, cutoff),
            ).fetchone()
            return int(row["total_units"] or 0)
