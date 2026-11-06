"""Pydantic data models for SQLite persistence entities."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class TemplateRecord(BaseModel):
    template_id: str
    name: str
    subject: str
    body_text: str | None = None
    body_html: str | None = None
    variables: list[str] = Field(default_factory=list)
    defaults: dict[str, str] = Field(default_factory=dict)
    attachments: list[str] = Field(default_factory=list)
    version: int = 1
    created_at: str
    updated_at: str


class SendLedgerRecord(BaseModel):
    operation_id: str
    idempotency_key: str
    plan_id: str | None = None
    operation_type: str
    recipient: str
    cc_count: int = 0
    bcc_count: int = 0
    total_recipient_count: int = 1
    thread_id: str | None = None
    gmail_message_id: str | None = None
    rfc_message_id: str | None = None
    status: Literal["PENDING", "SENT", "FAILED", "INDETERMINATE", "SKIPPED"]
    attempt_count: int = 1
    quota_cost: int = 100
    error_code: str | None = None
    error_message: str | None = None
    created_at: str
    updated_at: str
    epoch_seconds: float


class BulkPlanRecord(BaseModel):
    plan_id: str
    plan_type: Literal["bulk_send", "bulk_reply"]
    plan_hash: str
    confirmation_phrase: str
    requested_count: int
    total_recipient_count: int
    estimated_quota_units: int
    items: list[dict[str, Any]]
    status: Literal[
        "PREPARED",
        "EXECUTING",
        "COMPLETED",
        "PARTIAL_FAILURE",
        "PAUSED_LIMIT_REACHED",
        "INVALIDATED",
    ]
    created_at: str
    expires_at: str
    executed_at: str | None = None


class ScheduledJobRecord(BaseModel):
    job_id: str
    idempotency_key: str
    scheduled_at: str
    scheduled_epoch: float
    template_id: str | None = None
    recipient: str
    cc: list[str] = Field(default_factory=list)
    bcc: list[str] = Field(default_factory=list)
    reply_to: str | None = None
    subject: str | None = None
    body_text: str | None = None
    body_html: str | None = None
    variables: dict[str, Any] = Field(default_factory=dict)
    attachments: list[str] = Field(default_factory=list)
    thread_id: str | None = None
    status: Literal["PENDING", "PROCESSING", "SENT", "FAILED", "CANCELLED"] = "PENDING"
    attempt_count: int = 0
    gmail_message_id: str | None = None
    last_result: dict[str, Any] | None = None
    created_at: str
    updated_at: str


class SyncStateRecord(BaseModel):
    account_key: str = "default"
    email_address: str | None = None
    history_id: str
    watch_expiration: str | None = None
    topic_name: str | None = None
    last_sync_at: str
    updated_at: str
