"""Layer 1 Unit Tests: MIME, Base64URL, Templates, OAuth, Quota, Rate Limiter, Retries, and SQLite."""

from __future__ import annotations

import asyncio
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials

from gmail_mcp.auth.credentials import SCOPE_GMAIL_MODIFY
from gmail_mcp.auth.token_manager import TokenManager
from gmail_mcp.config import QUOTA_COST_REGISTRY, GmailConfig, get_quota_cost
from gmail_mcp.core.gmail_client import GmailClientWrapper
from gmail_mcp.core.pagination import build_gmail_search_query
from gmail_mcp.core.quota_manager import QuotaExhaustedError
from gmail_mcp.core.retry_manager import GmailMCPError
from gmail_mcp.core.safety import SafetyError
from tests.conftest import MockGmailBackend, make_http_error


def test_mime_creation_and_decoding_with_attachments(
    gmail_client: GmailClientWrapper,
    temp_workspace: dict[str, Path],
) -> None:
    """Verify MIME message creation, attachment encoding, base64url round-trip, and raw MIME decoding."""
    pdf_path = temp_workspace["resume_pdf"]
    built = gmail_client.mime_builder.build_email_message(
        to=["Alex <alex@example.com>"],
        cc=["cc@example.com"],
        bcc=["secret@example.com"],
        reply_to="replyto@example.com",
        subject="Application for AI Engineer",
        body_text="Hello Alex, plaintext body.",
        body_html="<p>Hello <b>Alex</b>, HTML body.</p>",
        attachments=[pdf_path],
    )

    assert built["raw"]
    assert built["rfc_message_id"].startswith("<")
    assert built["encoded_size_bytes"] > 0
    assert len(built["attachments"]) == 1
    assert built["attachments"][0]["filename"] == "resume.pdf"
    assert built["attachments"][0]["mime_type"] == "application/pdf"
    # BCC must never appear in public_summary
    assert "bcc" not in built["recipients"]["public_summary"]
    assert built["recipients"]["public_summary"]["bcc_count"] == 1

    parsed = gmail_client.mime_builder.parse_raw_mime(built["raw"])
    assert parsed["subject"] == "Application for AI Engineer"
    assert parsed["body_text"] is not None and "Hello Alex, plaintext body." in parsed["body_text"]
    assert parsed["body_html"] is not None and "<b>Alex</b>" in parsed["body_html"]
    assert len(parsed["attachments"]) == 1
    assert parsed["attachments"][0]["filename"] == "resume.pdf"
    assert parsed["attachments"][0]["data"] == pdf_path.read_bytes()


def test_template_substitution_and_html_escaping(gmail_client: GmailClientWrapper) -> None:
    """Verify template placeholder substitution, default values, missing variable rejection, and HTML escaping."""
    rendered = gmail_client.safety.render_template_content(
        subject="Hello {{name}} - {{role|Engineer}}",
        body_text="Hi {{name}}, role: {{role|Engineer}}",
        body_html="<p>Hi {{name}}, bio: {{bio}}</p>",
        variables={"name": "Sam", "bio": '<script>alert("xss")</script>'},
    )
    assert rendered["subject"] == "Hello Sam - Engineer"
    assert rendered["body_text"] == "Hi Sam, role: Engineer"
    assert rendered["body_html"] == "<p>Hi Sam, bio: &lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;</p>"

    # Missing required variable must raise SafetyError and never silently substitute empty string
    with pytest.raises(SafetyError) as exc_info:
        gmail_client.safety.render_template_content(
            subject="Application for {{position}}",
            body_text="Dear {{hiring_manager}}",
            body_html=None,
            variables={"position": "ML Engineer"},
        )
    assert exc_info.value.error_code == "MISSING_TEMPLATE_VARIABLE"
    assert "hiring_manager" in exc_info.value.details["missing_variables"]


def test_query_builder_preserves_native_gmail_syntax() -> None:
    """Verify query builder preserves valid raw Gmail syntax and combines structured filters."""
    q1 = build_gmail_search_query("from:john@example.com is:unread")
    assert q1 == "from:john@example.com is:unread"

    q2 = build_gmail_search_query(
        sender="recruiter@company.com",
        subject="Interview Invitation",
        has_attachment=True,
        is_unread=True,
        rfc822msgid="<abc123@mail.gmail.com>",
    )
    assert "from:recruiter@company.com" in q2
    assert 'subject:"Interview Invitation"' in q2
    assert "has:attachment" in q2
    assert "is:unread" in q2
    assert "rfc822msgid:abc123@mail.gmail.com" in q2


def test_oauth_token_lifecycle_refresh_revoked_and_missing_scope(
    temp_workspace: dict[str, Path],
) -> None:
    """Test valid token, expired token auto-refresh, revoked token error, and missing scope detection."""
    root = temp_workspace["root"]
    cfg = GmailConfig(
        workspace_root=root,
        credentials_path=root / "credentials.json",
        token_path=root / "token_test.json",
        db_path=root / "oauth_test.db",
    )
    mgr = TokenManager(cfg)

    # 1. Missing token file
    status_missing = mgr.get_auth_status()
    assert not status_missing.authenticated
    assert status_missing.status_code == "TOKEN_MISSING"

    # 2. Valid token with only gmail.modify (missing gmail.settings.basic)
    partial_creds = Credentials(
        token="valid_tok",
        refresh_token="ref_tok",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="cid",
        client_secret="csec",
        scopes=[SCOPE_GMAIL_MODIFY],
        expiry=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
    )
    mgr.save_credentials(partial_creds)
    mgr.clear_cached_credentials()

    status_partial = mgr.get_auth_status()
    assert status_partial.authenticated
    assert status_partial.status_code == "AUTHENTICATED_PARTIAL_SCOPES"
    assert "https://www.googleapis.com/auth/gmail.settings.basic" in status_partial.missing_default_scopes

    ok_filter, report_filter = mgr.has_scope_for_tool("gmail_create_filter")
    assert not ok_filter
    assert report_filter is not None
    assert report_filter["missing_scope"] == "https://www.googleapis.com/auth/gmail.settings.basic"
    assert report_filter["tool_requiring_scope"] == "gmail_create_filter"

    # 3. Expired token auto-refresh
    expired_creds = Credentials(
        token="expired_tok",
        refresh_token="ref_tok",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="cid",
        client_secret="csec",
        scopes=[SCOPE_GMAIL_MODIFY],
        expiry=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1),
    )
    mgr.save_credentials(expired_creds)
    mgr.clear_cached_credentials()

    def _fake_refresh(self_creds: Credentials, _req: object) -> None:
        self_creds.token = "refreshed_tok"
        self_creds.expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)

    with patch.object(Credentials, "refresh", _fake_refresh):
        refreshed_status = mgr.get_auth_status(auto_refresh=True)
        assert refreshed_status.authenticated

    # 4. Revoked token during refresh
    mgr.save_credentials(expired_creds)
    mgr.clear_cached_credentials()
    with patch.object(Credentials, "refresh", side_effect=RefreshError("Token has been expired or revoked.")):
        revoked_status = mgr.get_auth_status(auto_refresh=True)
        assert not revoked_status.authenticated
        assert revoked_status.status_code == "TOKEN_REVOKED_OR_EXPIRED"


def test_quota_calculation_and_per_user_per_project_limits(
    gmail_client: GmailClientWrapper,
) -> None:
    """Verify quota cost registry values and per-user/per-project quota ceiling enforcement."""
    assert get_quota_cost("messages.list") == 5
    assert get_quota_cost("messages.get", fmt="metadata") == 20
    assert get_quota_cost("messages.get", fmt="full") == 60
    assert get_quota_cost("messages.send") == 100
    assert get_quota_cost("threads.get", fmt="full") == 120
    assert get_quota_cost("messages.batchModify") == 50
    assert "settings.filters.create" in QUOTA_COST_REGISTRY

    # Set a tight user quota ceiling and verify QuotaExhaustedError is raised
    gmail_client.config.user_quota_per_minute = 250  # effective with 20% margin = 200 units
    gmail_client.quota_manager.check_and_consume_quota("messages.send", units=100)
    gmail_client.quota_manager.check_and_consume_quota("messages.send", units=100)
    with pytest.raises(QuotaExhaustedError) as exc_info:
        gmail_client.quota_manager.check_and_consume_quota("messages.list", units=5)
    assert exc_info.value.error_code == "USER_RATE_LIMIT_HEADROOM_EXCEEDED"


@pytest.mark.asyncio()
async def test_rate_limiter_enforces_concurrency_bounds(
    gmail_client: GmailClientWrapper,
) -> None:
    """Verify concurrent requests cannot exceed configured read/write/send concurrency limits."""
    gmail_client.config.read_concurrency = 2
    gmail_client.rate_limiter._read_sem = asyncio.Semaphore(2)

    async def _worker() -> None:
        async with gmail_client.rate_limiter.throttle("messages.list", op_class="read"):
            await asyncio.sleep(0.02)

    await asyncio.gather(*[_worker() for _ in range(8)])
    metrics = gmail_client.rate_limiter.concurrency_metrics
    assert metrics["peak"]["read"] <= 2
    assert metrics["active"]["read"] == 0


@pytest.mark.asyncio()
async def test_retry_manager_handles_429_403_and_5xx(
    gmail_client: GmailClientWrapper,
) -> None:
    """Verify RetryManager retries 429, 403 rateLimitExceeded, and 503, and does NOT retry 403 insufficientPermissions."""
    attempts = 0
    delays: list[float] = []

    async def _flaky_op() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise make_http_error(429, "rateLimitExceeded", "Too many requests", retry_after="0.01")
        if attempts == 2:
            raise make_http_error(403, "userRateLimitExceeded", "User rate limit exceeded")
        if attempts == 3:
            raise make_http_error(503, "backendError", "Service unavailable")
        return "ok"

    async def _record_sleep(d: float) -> None:
        delays.append(d)

    result, retry_count = await gmail_client.retry_manager.execute_with_retry(
        _flaky_op,
        tool_name="gmail_search_messages",
        sleep_fn=_record_sleep,
    )
    assert result == "ok"
    assert retry_count == 3
    assert len(delays) == 3

    # Non-retryable 403 insufficientPermissions must fail immediately with 0 retries
    perm_attempts = 0

    def _perm_fail() -> None:
        nonlocal perm_attempts
        perm_attempts += 1
        raise make_http_error(403, "insufficientPermissions", "Insufficient Authentication Scopes")

    with pytest.raises(GmailMCPError) as exc_info:
        await gmail_client.retry_manager.execute_with_retry(
            _perm_fail,
            tool_name="gmail_create_filter",
            sleep_fn=_record_sleep,
        )
    assert exc_info.value.error_code == "INSUFFICIENT_SCOPE"
    assert perm_attempts == 1


@pytest.mark.asyncio()
async def test_ambiguous_send_timeout_never_blindly_resends(
    gmail_client: GmailClientWrapper,
    mock_backend: MockGmailBackend,
) -> None:
    """
    Critical rule: Timeout after possible send success must NEVER trigger a blind resend.
    Case A: Message actually landed in Sent before timeout -> reconciled as SENT without second send.
    Case B: Message did not land -> raises INDETERMINATE_SEND_STATUS without second send.
    """
    send_calls = 0
    orig_dispatch = mock_backend.dispatch

    def _timeout_after_saving_dispatch(method: str, params: dict) -> object:
        nonlocal send_calls
        if method == "messages.send":
            send_calls += 1
            # Simulate message succeeding on Google's side before socket timeout on response
            orig_dispatch(method, params)
            raise socket.timeout("Read timed out waiting for Gmail send response")
        return orig_dispatch(method, params)

    gmail_client.set_custom_dispatcher(_timeout_after_saving_dispatch)
    built = gmail_client.mime_builder.build_email_message(
        to="target@example.com",
        subject="Timeout Reconciliation Test",
        body_text="Checking reconciliation",
    )
    resp, _ = await gmail_client.send_raw_mime_safely(
        raw_b64=built["raw"],
        rfc_message_id=built["rfc_message_id"],
        recipient_summary="target@example.com",
        operation_type="send_email",
        idempotency_key="idem_timeout_reconciled",
        tool_name="gmail_send_email",
    )
    assert send_calls == 1
    assert resp.get("reconciled_after_timeout") is True

    # Case B: Timeout where message is NOT found in Sent -> must raise INDETERMINATE_SEND_STATUS after 1 attempt
    send_calls_b = 0

    def _timeout_without_saving_dispatch(method: str, params: dict) -> object:
        nonlocal send_calls_b
        if method == "messages.send":
            send_calls_b += 1
            raise socket.timeout("Network dropped before send completed")
        return orig_dispatch(method, params)

    gmail_client.set_custom_dispatcher(_timeout_without_saving_dispatch)
    built_b = gmail_client.mime_builder.build_email_message(
        to="target2@example.com",
        subject="Unconfirmed Timeout Test",
        body_text="Checking indeterminate status",
    )
    with pytest.raises(GmailMCPError) as exc_info:
        await gmail_client.send_raw_mime_safely(
            raw_b64=built_b["raw"],
            rfc_message_id=built_b["rfc_message_id"],
            recipient_summary="target2@example.com",
            operation_type="send_email",
            idempotency_key="idem_timeout_indeterminate",
            tool_name="gmail_send_email",
        )
    assert exc_info.value.error_code == "INDETERMINATE_SEND_STATUS"
    assert send_calls_b == 1
    ledger = gmail_client.send_ledger_repo.get_by_idempotency_key("idem_timeout_indeterminate")
    assert ledger is not None and ledger.status == "INDETERMINATE"
