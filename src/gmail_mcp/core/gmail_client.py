"""Shared authenticated Gmail REST API v1 client and dependency container."""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from typing import Any, Callable

from googleapiclient.discovery import build

from gmail_mcp.auth.token_manager import TokenManager
from gmail_mcp.config import GmailConfig
from gmail_mcp.core.mime_builder import MimeBuilder
from gmail_mcp.core.quota_manager import QuotaExhaustedError, QuotaManager
from gmail_mcp.core.rate_limiter import OperationClass, RateLimiter
from gmail_mcp.core.retry_manager import GmailMCPError, RetryManager
from gmail_mcp.core.safety import SafetyError, SafetyGuard
from gmail_mcp.storage.database import DatabaseManager
from gmail_mcp.storage.repositories import (
    BulkPlanRepository,
    QuotaWindowRepository,
    ScheduledJobRepository,
    SendLedgerRepository,
    SyncStateRepository,
    TemplateRepository,
)


class GmailClientWrapper:
    """
    Central dependency-injected Gmail API client shared across all MCP tools.
    Coordinates OAuth credentials, weighted rate limiting, quota accounting,
    classified retries, ambiguous send reconciliation, and SQLite persistence.
    """

    def __init__(
        self,
        config: GmailConfig | None = None,
        db: DatabaseManager | None = None,
        token_manager: TokenManager | None = None,
        custom_dispatcher: Callable[[str, dict[str, Any]], Any] | None = None,
    ) -> None:
        self.config = config or GmailConfig()
        self.config.ensure_directories()

        self.db = db or DatabaseManager(self.config.db_path)
        self.token_manager = token_manager or TokenManager(self.config)

        # Repositories
        self.template_repo = TemplateRepository(self.db)
        self.send_ledger_repo = SendLedgerRepository(self.db)
        self.bulk_plan_repo = BulkPlanRepository(self.db)
        self.scheduled_job_repo = ScheduledJobRepository(self.db)
        self.sync_state_repo = SyncStateRepository(self.db)
        self.quota_repo = QuotaWindowRepository(self.db)

        # Core managers
        self.safety = SafetyGuard(self.config)
        self.mime_builder = MimeBuilder(self.safety)
        self.quota_manager = QuotaManager(self.config, self.quota_repo, self.send_ledger_repo)
        self.rate_limiter = RateLimiter(self.config, self.quota_manager)
        self.retry_manager = RetryManager(self.config, self.token_manager)

        # Optional mock/test dispatcher: fn(method_name, params_dict) -> response_dict
        self._custom_dispatcher = custom_dispatcher
        self._thread_local = threading.local()

        # Short-lived safe metadata cache (never caches email bodies)
        self._profile_cache: tuple[float, dict[str, Any]] | None = None
        self._labels_cache: tuple[float, list[dict[str, Any]]] | None = None
        self._cache_ttl_seconds = 30.0

    def set_custom_dispatcher(self, dispatcher: Callable[[str, dict[str, Any]], Any] | None) -> None:
        """Inject a mock API dispatcher for unit and stress testing."""
        self._custom_dispatcher = dispatcher
        self.invalidate_metadata_cache()

    def invalidate_metadata_cache(self) -> None:
        self._profile_cache = None
        self._labels_cache = None

    def _get_thread_service(self) -> Any:
        """Return a thread-local googleapiclient Gmail v1 Resource sharing refreshed credentials."""
        creds = self.token_manager.load_credentials(auto_refresh=True)
        if creds is None or not creds.valid:
            raise GmailMCPError(
                "AUTH_NOT_CONFIGURED",
                (
                    f"Valid OAuth credentials not found at '{self.config.token_path.name}'. "
                    "Authenticate first via `uv run --no-project python -m gmail_mcp.auth.oauth --login`."
                ),
                http_status=401,
                retryable=False,
            )
        service = getattr(self._thread_local, "gmail_service", None)
        cached_token = getattr(self._thread_local, "access_token", None)
        if service is None or cached_token != creds.token:
            service = build("gmail", "v1", credentials=creds, cache_discovery=False)
            self._thread_local.gmail_service = service
            self._thread_local.access_token = creds.token
        return service

    def _refresh_token_sync(self) -> bool:
        try:
            self.token_manager.clear_cached_credentials()
            self._thread_local.gmail_service = None
            creds = self.token_manager.load_credentials(auto_refresh=True)
            return bool(creds and creds.valid)
        except Exception:
            return False

    async def execute_api(
        self,
        method: str,
        params: dict[str, Any],
        *,
        tool_name: str,
        op_class: OperationClass = "read",
        fmt: str | None = None,
        units_override: int | None = None,
        attachment_bytes: int = 0,
        is_send_operation: bool = False,
        reconcile_send_fn: Callable[[], Any] | None = None,
    ) -> tuple[Any, dict[str, Any]]:
        """
        Execute a Gmail REST API v1 method with scope validation, rate limiting,
        quota tracking, classified retries, and redacting audit logging.
        """
        op_id = f"req_{uuid.uuid4().hex[:12]}"
        start_t = time.perf_counter()

        # Pre-flight scope check when token is present
        require_full = method in ("messages.delete", "threads.delete", "messages.batchDelete")
        has_scope, scope_err = self.token_manager.has_scope_for_tool(
            tool_name, require_full_access=require_full
        )
        if not has_scope and scope_err is not None:
            raise GmailMCPError(
                "INSUFFICIENT_SCOPE",
                f"Missing required OAuth scope '{scope_err['missing_scope']}' for tool '{tool_name}'.",
                http_status=403,
                retryable=False,
                details=scope_err,
            )

        consumed_units = 0
        retry_count = 0
        try:
            async with self.rate_limiter.throttle(
                method=method,
                op_class=op_class,
                fmt=fmt,
                units_override=units_override,
                attachment_bytes=attachment_bytes,
                enforce_send_interval=is_send_operation,
            ) as units:
                consumed_units = units

                async def _invoke() -> Any:
                    if self._custom_dispatcher is not None:
                        res = self._custom_dispatcher(method, params)
                        if asyncio.iscoroutine(res):
                            return await res
                        return res
                    return await asyncio.to_thread(self._dispatch_live_googleapi, method, params)

                result, retry_count = await self.retry_manager.execute_with_retry(
                    _invoke,
                    tool_name=tool_name,
                    is_send_operation=is_send_operation,
                    reconcile_send_fn=reconcile_send_fn,
                    on_token_refresh_fn=lambda: asyncio.to_thread(self._refresh_token_sync),
                )

            duration_ms = (time.perf_counter() - start_t) * 1000.0
            meta = self.safety.log_operation(
                tool_name=tool_name,
                operation_id=op_id,
                api_method=method,
                duration_ms=duration_ms,
                http_status=200,
                quota_units=consumed_units,
                success=True,
                retry_count=retry_count,
            )
            return result, meta

        except (GmailMCPError, QuotaExhaustedError, SafetyError) as exc:
            duration_ms = (time.perf_counter() - start_t) * 1000.0
            http_status = getattr(exc, "http_status", 429 if isinstance(exc, QuotaExhaustedError) else 400)
            error_code = getattr(exc, "error_code", "ERROR")
            self.safety.log_operation(
                tool_name=tool_name,
                operation_id=op_id,
                api_method=method,
                duration_ms=duration_ms,
                http_status=http_status,
                quota_units=consumed_units,
                success=False,
                retry_count=retry_count,
                error_code=error_code,
            )
            raise

    def _dispatch_live_googleapi(self, method: str, params: dict[str, Any]) -> Any:
        """Dispatch a call to the official googleapiclient Gmail v1 resource."""
        service = self._get_thread_service()
        users = service.users()

        if method == "users.getProfile":
            return users.getProfile(userId="me").execute()
        if method == "watch":
            return users.watch(userId="me", body=params["body"]).execute()
        if method == "stop":
            return users.stop(userId="me").execute()
        if method == "history.list":
            return users.history().list(userId="me", **params).execute()

        # Messages
        if method == "messages.list":
            return users.messages().list(userId="me", **params).execute()
        if method == "messages.get":
            return users.messages().get(userId="me", **params).execute()
        if method == "messages.send":
            return users.messages().send(userId="me", body=params["body"]).execute()
        if method == "messages.modify":
            return users.messages().modify(userId="me", id=params["id"], body=params["body"]).execute()
        if method == "messages.batchModify":
            return users.messages().batchModify(userId="me", body=params["body"]).execute()
        if method == "messages.batchDelete":
            return users.messages().batchDelete(userId="me", body=params["body"]).execute()
        if method == "messages.trash":
            return users.messages().trash(userId="me", id=params["id"]).execute()
        if method == "messages.untrash":
            return users.messages().untrash(userId="me", id=params["id"]).execute()
        if method == "messages.delete":
            return users.messages().delete(userId="me", id=params["id"]).execute()
        if method == "messages.attachments.get":
            return (
                users.messages()
                .attachments()
                .get(userId="me", messageId=params["messageId"], id=params["id"])
                .execute()
            )

        # Threads
        if method == "threads.list":
            return users.threads().list(userId="me", **params).execute()
        if method == "threads.get":
            return users.threads().get(userId="me", **params).execute()
        if method == "threads.modify":
            return users.threads().modify(userId="me", id=params["id"], body=params["body"]).execute()
        if method == "threads.trash":
            return users.threads().trash(userId="me", id=params["id"]).execute()
        if method == "threads.untrash":
            return users.threads().untrash(userId="me", id=params["id"]).execute()
        if method == "threads.delete":
            return users.threads().delete(userId="me", id=params["id"]).execute()

        # Drafts
        if method == "drafts.create":
            return users.drafts().create(userId="me", body=params["body"]).execute()
        if method == "drafts.get":
            return users.drafts().get(userId="me", **params).execute()
        if method == "drafts.list":
            return users.drafts().list(userId="me", **params).execute()
        if method == "drafts.update":
            return users.drafts().update(userId="me", id=params["id"], body=params["body"]).execute()
        if method == "drafts.delete":
            return users.drafts().delete(userId="me", id=params["id"]).execute()
        if method == "drafts.send":
            return users.drafts().send(userId="me", body=params["body"]).execute()

        # Labels
        if method == "labels.list":
            return users.labels().list(userId="me").execute()
        if method == "labels.get":
            return users.labels().get(userId="me", id=params["id"]).execute()
        if method == "labels.create":
            return users.labels().create(userId="me", body=params["body"]).execute()
        if method == "labels.update":
            return users.labels().update(userId="me", id=params["id"], body=params["body"]).execute()
        if method == "labels.patch":
            return users.labels().patch(userId="me", id=params["id"], body=params["body"]).execute()
        if method == "labels.delete":
            return users.labels().delete(userId="me", id=params["id"]).execute()

        # Settings & Filters
        if method == "settings.filters.list":
            return users.settings().filters().list(userId="me").execute()
        if method == "settings.filters.get":
            return users.settings().filters().get(userId="me", id=params["id"]).execute()
        if method == "settings.filters.create":
            return users.settings().filters().create(userId="me", body=params["body"]).execute()
        if method == "settings.filters.delete":
            return users.settings().filters().delete(userId="me", id=params["id"]).execute()
        if method == "settings.getVacation":
            return users.settings().getVacation(userId="me").execute()
        if method == "settings.updateVacation":
            return users.settings().updateVacation(userId="me", body=params["body"]).execute()
        if method == "settings.getAutoForwarding":
            return users.settings().getAutoForwarding(userId="me").execute()
        if method == "settings.sendAs.list":
            return users.settings().sendAs().list(userId="me").execute()
        if method == "settings.sendAs.get":
            return users.settings().sendAs().get(userId="me", sendAsEmail=params["sendAsEmail"]).execute()

        raise ValueError(f"Unsupported Gmail API method: {method}")

    # --- High-Level Reusable Operations ---

    async def get_profile(self, tool_name: str = "gmail_get_profile", use_cache: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
        if use_cache and self._profile_cache is not None:
            ts, cached = self._profile_cache
            if (time.monotonic() - ts) < self._cache_ttl_seconds:
                return cached, {"quota_estimate": 0, "cached": True}
        resp, meta = await self.execute_api("users.getProfile", {}, tool_name=tool_name, op_class="read")
        self._profile_cache = (time.monotonic(), resp)
        return resp, meta

    async def reconcile_sent_mail_24h(self, tool_name: str = "gmail_prepare_bulk_send") -> int | None:
        """Query `in:sent` over the past 24 hours to account for messages sent outside the MCP server."""
        try:
            cutoff_epoch = int(time.time() - 86400)
            resp, _ = await self.execute_api(
                "messages.list",
                {"q": f"in:sent after:{cutoff_epoch}", "maxResults": 500},
                tool_name=tool_name,
                op_class="read",
            )
            msgs = resp.get("messages") or []
            estimated = max(len(msgs), int(resp.get("resultSizeEstimate") or 0))
            self.quota_manager.update_reconciled_sent_count(estimated)
            return estimated
        except Exception:
            return None

    async def verify_message_sent_by_rfc_id(
        self, rfc_message_id: str, tool_name: str = "gmail_send_email"
    ) -> dict[str, Any] | None:
        """Check if a message with `rfc_message_id` was already delivered to Sent mail after an ambiguous timeout."""
        clean_id = rfc_message_id.strip().strip("<>")
        resp, _ = await self.execute_api(
            "messages.list",
            {"q": f"rfc822msgid:{clean_id}", "maxResults": 5},
            tool_name=tool_name,
            op_class="read",
        )
        msgs = resp.get("messages") or []
        if msgs:
            first = msgs[0]
            return {
                "id": first.get("id"),
                "threadId": first.get("threadId"),
                "reconciled_after_timeout": True,
            }
        return None

    async def send_raw_mime_safely(
        self,
        *,
        raw_b64: str,
        rfc_message_id: str,
        recipient_summary: str,
        operation_type: str,
        idempotency_key: str,
        tool_name: str,
        thread_id: str | None = None,
        cc_count: int = 0,
        bcc_count: int = 0,
        total_recipient_count: int = 1,
        plan_id: str | None = None,
        attachment_bytes: int = 0,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """
        Send a raw base64url MIME message with idempotency protection, daily send cap enforcement,
        and ambiguous-timeout reconciliation.
        """
        existing = self.send_ledger_repo.get_by_idempotency_key(idempotency_key)
        if existing and existing.status in ("SENT", "INDETERMINATE"):
            return (
                {
                    "id": existing.gmail_message_id,
                    "threadId": existing.thread_id,
                    "status": existing.status,
                    "deduplicated": True,
                    "idempotency_key": idempotency_key,
                },
                {"quota_estimate": 0, "deduplicated": True},
            )

        # Enforce daily sending soft/hard caps before transmitting
        self.quota_manager.require_sending_headroom(
            requested_messages=1,
            requested_recipients=total_recipient_count,
        )

        body: dict[str, Any] = {"raw": raw_b64}
        if thread_id:
            body["threadId"] = thread_id

        async def _reconcile() -> dict[str, Any] | None:
            return await self.verify_message_sent_by_rfc_id(rfc_message_id, tool_name=tool_name)

        try:
            resp, meta = await self.execute_api(
                "messages.send",
                {"body": body},
                tool_name=tool_name,
                op_class="send",
                attachment_bytes=attachment_bytes,
                is_send_operation=True,
                reconcile_send_fn=_reconcile,
            )
            self.send_ledger_repo.record_send(
                idempotency_key=idempotency_key,
                operation_type=operation_type,
                recipient=recipient_summary,
                status="SENT",
                cc_count=cc_count,
                bcc_count=bcc_count,
                total_recipient_count=total_recipient_count,
                plan_id=plan_id,
                thread_id=resp.get("threadId") or thread_id,
                gmail_message_id=resp.get("id"),
                rfc_message_id=rfc_message_id,
                quota_cost=100,
            )
            return resp, meta
        except GmailMCPError as exc:
            ledger_status = "INDETERMINATE" if exc.error_code == "INDETERMINATE_SEND_STATUS" else "FAILED"
            self.send_ledger_repo.record_send(
                idempotency_key=idempotency_key,
                operation_type=operation_type,
                recipient=recipient_summary,
                status=ledger_status,
                cc_count=cc_count,
                bcc_count=bcc_count,
                total_recipient_count=total_recipient_count,
                plan_id=plan_id,
                thread_id=thread_id,
                rfc_message_id=rfc_message_id,
                quota_cost=100,
                error_code=exc.error_code,
                error_message=exc.message,
            )
            raise

    @staticmethod
    def format_tool_error(operation: str, exc: Exception, mutates_mailbox: bool = False) -> dict[str, Any]:
        """Format any exception into the standardized MCP tool failure contract."""
        if isinstance(exc, GmailMCPError):
            return {
                "success": False,
                "operation": operation,
                "mutates_mailbox": mutates_mailbox,
                "error_code": exc.error_code,
                "message": exc.message,
                "http_status": exc.http_status,
                "retryable": exc.retryable,
                "details": exc.details,
            }
        if isinstance(exc, QuotaExhaustedError):
            return {
                "success": False,
                "operation": operation,
                "mutates_mailbox": mutates_mailbox,
                "error_code": exc.error_code,
                "message": exc.message,
                "http_status": 429,
                "retryable": False,
                "details": exc.details,
            }
        if isinstance(exc, SafetyError):
            return {
                "success": False,
                "operation": operation,
                "mutates_mailbox": mutates_mailbox,
                "error_code": exc.error_code,
                "message": exc.message,
                "http_status": 400,
                "retryable": False,
                "details": exc.details,
            }
        return {
            "success": False,
            "operation": operation,
            "mutates_mailbox": mutates_mailbox,
            "error_code": "INTERNAL_ERROR",
            "message": f"{type(exc).__name__}: {exc}",
            "http_status": 500,
            "retryable": False,
        }
