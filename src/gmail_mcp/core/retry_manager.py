"""Centralized HTTP/network error classification, exponential backoff, and ambiguous-send reconciliation."""

from __future__ import annotations

import asyncio
import json
import random
import socket
from typing import Any, Awaitable, Callable

from googleapiclient.errors import HttpError

from gmail_mcp.auth.credentials import build_insufficient_scope_report
from gmail_mcp.auth.token_manager import TokenManager
from gmail_mcp.config import GmailConfig


class GmailMCPError(RuntimeError):
    """Structured operational error with classification metadata for MCP tool responses."""

    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        http_status: int = 500,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.http_status = http_status
        self.retryable = retryable
        self.details = details or {}


class RetryManager:
    """Executes Gmail API operations with classified error handling and safe retry semantics."""

    def __init__(self, config: GmailConfig, token_manager: TokenManager) -> None:
        self.config = config
        self.token_manager = token_manager

    def compute_backoff_delay(self, attempt: int, retry_after_header: str | None = None) -> float:
        """Truncated exponential backoff with jitter: min(2 ** attempt + random.uniform(0, 1), 64)."""
        if retry_after_header:
            try:
                parsed = float(retry_after_header)
                if parsed > 0:
                    return min(parsed, self.config.max_backoff_seconds)
            except ValueError:
                pass
        delay = (2**attempt) + random.uniform(0.0, 1.0)
        return float(min(delay, self.config.max_backoff_seconds))

    def classify_exception(self, exc: Exception, tool_name: str = "gmail_tool") -> GmailMCPError:
        """Classify an exception into a structured GmailMCPError per Section 7.F."""
        if isinstance(exc, GmailMCPError):
            return exc

        if isinstance(exc, (socket.timeout, TimeoutError, asyncio.TimeoutError)):
            return GmailMCPError(
                "NETWORK_TIMEOUT",
                f"Gmail API request timed out: {exc}",
                http_status=504,
                retryable=True,
            )

        if isinstance(exc, HttpError):
            status = int(getattr(exc.resp, "status", 500))
            reason = ""
            message = str(exc)
            retry_after = exc.resp.get("retry-after") if hasattr(exc, "resp") and exc.resp else None

            try:
                payload = json.loads(exc.content.decode("utf-8", errors="replace"))
                err_obj = payload.get("error") or {}
                message = err_obj.get("message") or message
                errors_list = err_obj.get("errors") or []
                if errors_list and isinstance(errors_list[0], dict):
                    reason = errors_list[0].get("reason") or ""
            except Exception:
                pass

            reason_lower = reason.lower()
            msg_lower = message.lower()

            if status == 400:
                return GmailMCPError(
                    "INVALID_ARGUMENT",
                    f"Gmail API rejected the request (400 Bad Request): {message}",
                    http_status=400,
                    retryable=False,
                    details={"reason": reason},
                )

            if status == 401:
                return GmailMCPError(
                    "AUTH_REAUTHORIZATION_REQUIRED",
                    "OAuth access token was rejected (401 Unauthorized) after refresh attempt. Reauthorize the account.",
                    http_status=401,
                    retryable=False,
                    details={
                        "reason": reason,
                        "reauthorization_command": "uv run --no-project python -m gmail_mcp.auth.oauth --login --force-reauth",
                    },
                )

            if status == 403:
                if (
                    "insufficientpermissions" in reason_lower
                    or "insufficient authentication scopes" in msg_lower
                    or "accessnotconfigured" in reason_lower
                ):
                    scope_report = build_insufficient_scope_report(
                        tool_name, self.token_manager.get_granted_scopes()
                    )
                    return GmailMCPError(
                        "INSUFFICIENT_SCOPE",
                        f"Insufficient OAuth permissions for '{tool_name}': {message}",
                        http_status=403,
                        retryable=False,
                        details=scope_report,
                    )
                if "dailylimitexceeded" in reason_lower or "sending limit" in msg_lower or "limit exceeded" in msg_lower and "send" in msg_lower:
                    return GmailMCPError(
                        "DAILY_SEND_LIMIT",
                        f"Gmail daily sending limit reached (403): {message}",
                        http_status=403,
                        retryable=False,
                        details={"reason": reason},
                    )
                if "ratelimitexceeded" in reason_lower or "userratelimitexceeded" in reason_lower or "quotaexceeded" in reason_lower:
                    return GmailMCPError(
                        "RATE_LIMIT_EXCEEDED",
                        f"Gmail API rate limit reached (403 {reason}): {message}",
                        http_status=403,
                        retryable=True,
                        details={"reason": reason, "retry_after": retry_after},
                    )
                return GmailMCPError(
                    "FORBIDDEN",
                    f"Gmail API returned 403 Forbidden ({reason or 'unknown'}): {message}",
                    http_status=403,
                    retryable=False,
                    details={"reason": reason},
                )

            if status == 404:
                return GmailMCPError(
                    "RESOURCE_NOT_FOUND",
                    f"Requested Gmail resource was not found or is stale (404): {message}",
                    http_status=404,
                    retryable=False,
                    details={"reason": reason},
                )

            if status == 429:
                if "daily" in msg_lower and "send" in msg_lower:
                    return GmailMCPError(
                        "DAILY_SEND_LIMIT",
                        f"Gmail daily sending limit reached (429): {message}",
                        http_status=429,
                        retryable=False,
                        details={"reason": reason},
                    )
                return GmailMCPError(
                    "TOO_MANY_REQUESTS",
                    f"Gmail API rate limit exceeded (429): {message}",
                    http_status=429,
                    retryable=True,
                    details={"reason": reason, "retry_after": retry_after},
                )

            if status in (500, 502, 503, 504):
                return GmailMCPError(
                    "TRANSIENT_SERVER_ERROR",
                    f"Gmail API transient server error ({status}): {message}",
                    http_status=status,
                    retryable=True,
                    details={"reason": reason, "retry_after": retry_after},
                )

            return GmailMCPError(
                f"HTTP_{status}",
                f"Gmail API error ({status}): {message}",
                http_status=status,
                retryable=False,
                details={"reason": reason},
            )

        return GmailMCPError(
            "UNEXPECTED_ERROR",
            f"{type(exc).__name__}: {exc}",
            http_status=500,
            retryable=False,
        )

    async def execute_with_retry(
        self,
        operation_fn: Callable[[], Awaitable[Any] | Any],
        *,
        tool_name: str,
        is_send_operation: bool = False,
        reconcile_send_fn: Callable[[], Awaitable[dict[str, Any] | None]] | None = None,
        on_token_refresh_fn: Callable[[], Awaitable[bool] | bool] | None = None,
        sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> tuple[Any, int]:
        """
        Execute an operation with classified retry handling.

        Critical rule for sending operations:
        Never automatically resend an email after an ambiguous timeout or 5xx error.
        First reconcile whether the message was already sent via `reconcile_send_fn`.
        """
        attempt = 0
        refreshed_401 = False

        while True:
            try:
                res = operation_fn()
                if asyncio.iscoroutine(res):
                    res = await res
                return res, attempt
            except Exception as exc:
                classified = self.classify_exception(exc, tool_name=tool_name)

                # Handle 401 once via OAuth token refresh
                if classified.http_status == 401 and not refreshed_401 and on_token_refresh_fn is not None:
                    refreshed_401 = True
                    refresh_ok = on_token_refresh_fn()
                    if asyncio.iscoroutine(refresh_ok):
                        refresh_ok = await refresh_ok
                    if refresh_ok:
                        attempt += 1
                        continue
                    raise classified from exc

                # Critical rule: NEVER blindly retry a send operation after a timeout or 5xx!
                if is_send_operation and classified.error_code in ("NETWORK_TIMEOUT", "TRANSIENT_SERVER_ERROR"):
                    if reconcile_send_fn is not None:
                        try:
                            reconciled = await reconcile_send_fn()
                            if reconciled is not None:
                                return reconciled, attempt
                        except Exception:
                            pass
                    raise GmailMCPError(
                        "INDETERMINATE_SEND_STATUS",
                        (
                            "Ambiguous timeout or transient server error occurred during email send, and "
                            "delivery could not be confirmed via Sent-mail reconciliation. "
                            "Automatic resend is blocked to prevent duplicate emails."
                        ),
                        http_status=classified.http_status,
                        retryable=False,
                        details={"original_error": classified.message, "reconciled": False},
                    ) from exc

                if not classified.retryable or attempt >= self.config.max_retries:
                    raise classified from exc

                retry_after = classified.details.get("retry_after")
                delay = self.compute_backoff_delay(attempt, retry_after_header=retry_after)
                attempt += 1
                await sleep_fn(delay)
