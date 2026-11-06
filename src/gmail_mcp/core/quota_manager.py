"""Quota cost calculation, rolling usage tracking, and daily sending headroom reconciliation."""

from __future__ import annotations

import time
from typing import Any

from gmail_mcp.config import QUOTA_COST_REGISTRY, GmailConfig, get_quota_cost
from gmail_mcp.storage.repositories import QuotaWindowRepository, SendLedgerRepository


class QuotaExhaustedError(RuntimeError):
    """Raised when an operation would exceed configured quota or daily sending safeguards."""

    def __init__(self, error_code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.details = details or {}


class QuotaManager:
    """Tracks Gmail API quota units and enforces application-level daily sending protection."""

    def __init__(
        self,
        config: GmailConfig,
        quota_repo: QuotaWindowRepository,
        send_ledger_repo: SendLedgerRepository,
    ) -> None:
        self.config = config
        self.quota_repo = quota_repo
        self.send_ledger_repo = send_ledger_repo
        self._last_reconciled_sent_count: int | None = None
        self._last_reconciled_at: float | None = None
        # Optional test hook to simulate quota exhaustion at a specific threshold
        self.simulated_unit_ceiling: int | None = None
        self.session_units_consumed: int = 0

    @staticmethod
    def estimate_cost(method: str, count: int = 1, fmt: str | None = None) -> dict[str, int]:
        """Calculate per-request and total quota units for an API method."""
        unit_cost = get_quota_cost(method, fmt=fmt)
        total = unit_cost * max(1, count)
        return {
            "units_per_request": unit_cost,
            "request_count": count,
            "total_units": total,
        }

    def check_and_consume_quota(self, method: str, units: int | None = None, fmt: str | None = None) -> int:
        """Verify per-user and per-project minute quota headroom and record consumption."""
        cost = units if units is not None else get_quota_cost(method, fmt=fmt)

        if self.simulated_unit_ceiling is not None:
            if self.session_units_consumed + cost > self.simulated_unit_ceiling:
                raise QuotaExhaustedError(
                    "QUOTA_EXHAUSTED",
                    f"Quota ceiling reached ({self.session_units_consumed}/{self.simulated_unit_ceiling} units).",
                    {
                        "session_units_consumed": self.session_units_consumed,
                        "requested_units": cost,
                        "ceiling": self.simulated_unit_ceiling,
                    },
                )

        user_used_60s = self.quota_repo.get_rolling_usage(window_seconds=60.0, bucket_scope="user")
        project_used_60s = self.quota_repo.get_rolling_usage(window_seconds=60.0, bucket_scope="project")

        if user_used_60s + cost > self.config.effective_user_quota_per_minute:
            raise QuotaExhaustedError(
                "USER_RATE_LIMIT_HEADROOM_EXCEEDED",
                (
                    f"Per-user 60s quota headroom exceeded: used {user_used_60s} + requested {cost} "
                    f"> effective cap {self.config.effective_user_quota_per_minute} units/min."
                ),
                {
                    "user_used_60s": user_used_60s,
                    "requested_units": cost,
                    "effective_user_cap_per_min": self.config.effective_user_quota_per_minute,
                },
            )

        if project_used_60s + cost > self.config.effective_project_quota_per_minute:
            raise QuotaExhaustedError(
                "PROJECT_RATE_LIMIT_HEADROOM_EXCEEDED",
                (
                    f"Per-project 60s quota headroom exceeded: used {project_used_60s} + requested {cost} "
                    f"> effective cap {self.config.effective_project_quota_per_minute} units/min."
                ),
                {
                    "project_used_60s": project_used_60s,
                    "requested_units": cost,
                    "effective_project_cap_per_min": self.config.effective_project_quota_per_minute,
                },
            )

        self.quota_repo.record_usage(method, cost, bucket_scope="user")
        self.quota_repo.record_usage(method, cost, bucket_scope="project")
        self.session_units_consumed += cost
        return cost

    def update_reconciled_sent_count(self, remote_sent_24h: int) -> None:
        """Record the latest 24h `in:sent` message count observed from the Gmail mailbox."""
        self._last_reconciled_sent_count = int(remote_sent_24h)
        self._last_reconciled_at = time.time()

    def get_sending_headroom(self, requested_messages: int = 0, requested_recipients: int = 0) -> dict[str, Any]:
        """Compute 24-hour sending usage, combining local SQLite ledger and reconciled Sent folder count."""
        ledger_stats = self.send_ledger_repo.get_24h_sent_stats()
        local_msgs = ledger_stats["messages_sent_24h"]
        local_recips = ledger_stats["recipients_sent_24h"]

        remote_msgs = self._last_reconciled_sent_count
        effective_sent = max(local_msgs, local_recips, remote_msgs or 0)

        soft_cap = self.config.daily_send_soft_cap
        hard_cap = self.config.daily_send_hard_cap
        external_ceiling = self.config.external_daily_send_ceiling

        remaining_before_soft = max(0, soft_cap - effective_sent)
        remaining_before_hard = max(0, hard_cap - effective_sent)
        needed = max(requested_messages, requested_recipients)

        allowed = (effective_sent + needed) <= hard_cap
        soft_warning = (effective_sent + needed) > soft_cap

        warnings: list[str] = []
        if remote_msgs is None:
            warnings.append(
                "Exact account-wide sending usage across web/mobile/SMTP has not been reconciled in this session; "
                "headroom is based on the local MCP SQLite send ledger."
            )
        if soft_warning and allowed:
            warnings.append(
                f"Operation will cross the configured daily_send_soft_cap ({soft_cap}); "
                f"hard cap is {hard_cap} (external Google account ceiling is {external_ceiling})."
            )

        return {
            "allowed": allowed,
            "effective_sent_24h": effective_sent,
            "local_ledger_messages_24h": local_msgs,
            "local_ledger_recipients_24h": local_recips,
            "reconciled_sent_folder_24h": remote_msgs,
            "reconciled_at_epoch": self._last_reconciled_at,
            "daily_send_soft_cap": soft_cap,
            "daily_send_hard_cap": hard_cap,
            "external_google_account_ceiling": external_ceiling,
            "remaining_before_soft_cap": remaining_before_soft,
            "remaining_before_hard_cap": remaining_before_hard,
            "requested_count": needed,
            "warnings": warnings,
        }

    def require_sending_headroom(self, requested_messages: int = 1, requested_recipients: int = 1) -> dict[str, Any]:
        """Raise QuotaExhaustedError if sending `requested_messages`/`requested_recipients` exceeds hard cap."""
        headroom = self.get_sending_headroom(
            requested_messages=requested_messages,
            requested_recipients=requested_recipients,
        )
        if not headroom["allowed"]:
            raise QuotaExhaustedError(
                "DAILY_SEND_LIMIT",
                "The configured sending threshold has been reached.",
                headroom,
            )
        return headroom

    def get_runtime_limits_report(self) -> dict[str, Any]:
        """Build a transparent report distinguishing Google's documented quota model from local estimates."""
        user_60s = self.quota_repo.get_rolling_usage(60.0, bucket_scope="user")
        project_60s = self.quota_repo.get_rolling_usage(60.0, bucket_scope="project")
        project_24h = self.quota_repo.get_rolling_usage(86400.0, bucket_scope="project")
        send_headroom = self.get_sending_headroom()

        return {
            "cloud_project": {
                "project_number": self.config.cloud_project_number,
                "created_before": self.config.cloud_project_created_before,
                "legacy_quota_verified": self.config.legacy_quota_verified,
                "quota_model_note": (
                    "Project 542762227233 was created before 2026-05-01, but legacy quota retention is not assumed. "
                    "Standard documented Gmail API v1 quotas are enforced unless verified in Google Cloud Console."
                ),
            },
            "google_documented_standard_quotas": {
                "per_project_per_minute_units": self.config.project_quota_per_minute,
                "per_user_per_project_per_minute_units": self.config.user_quota_per_minute,
                "per_project_daily_billing_threshold_units": self.config.daily_billing_threshold,
                "personal_gmail_external_daily_send_ceiling": self.config.external_daily_send_ceiling,
                "max_recipients_per_message": self.config.max_recipients_per_message,
                "note": (
                    "Gmail REST API does not expose a live quota-remaining or daily-send-counter endpoint. "
                    "All usage figures below are locally measured by this MCP server."
                ),
            },
            "locally_enforced_safeguards": {
                "quota_safety_margin": self.config.quota_safety_margin,
                "effective_user_quota_per_minute_units": self.config.effective_user_quota_per_minute,
                "effective_project_quota_per_minute_units": self.config.effective_project_quota_per_minute,
                "daily_send_soft_cap": self.config.daily_send_soft_cap,
                "daily_send_hard_cap": self.config.daily_send_hard_cap,
                "send_concurrency": self.config.send_concurrency,
                "send_interval_seconds": self.config.send_interval_seconds,
                "read_concurrency": self.config.read_concurrency,
                "write_concurrency": self.config.write_concurrency,
                "attachment_warning_size_mb": self.config.attachment_warning_size_mb,
                "attachment_max_size_mb": self.config.attachment_max_size_mb,
            },
            "locally_measured_usage": {
                "user_units_last_60s": user_60s,
                "user_units_headroom_60s": max(0, self.config.effective_user_quota_per_minute - user_60s),
                "project_units_last_60s": project_60s,
                "project_units_last_24h": project_24h,
                "session_units_consumed": self.session_units_consumed,
                "sending_headroom": send_headroom,
            },
            "method_cost_registry": QUOTA_COST_REGISTRY,
        }
