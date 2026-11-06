"""
Layer 2 Live Gmail Integration Tests (Gated behind environment variables per Section 11):
- GMAIL_E2E_ENABLED=1
- GMAIL_E2E_SEND_ENABLED=0 (default disabled)
- GMAIL_E2E_ALLOWED_RECIPIENTS=
Never performs live bulk sending during automated test runs.
"""

from __future__ import annotations

import os

import pytest

from gmail_mcp.config import GmailConfig
from gmail_mcp.core.gmail_client import GmailClientWrapper
from gmail_mcp.tools import (
    gmail_auth_status,
    gmail_create_draft,
    gmail_delete_draft,
    gmail_get_mailbox_stats,
    gmail_get_profile,
    gmail_list_labels,
    gmail_search_messages,
    gmail_send_email,
)

E2E_ENABLED = os.getenv("GMAIL_E2E_ENABLED", "0") == "1"
E2E_SEND_ENABLED = os.getenv("GMAIL_E2E_SEND_ENABLED", "0") == "1"


@pytest.mark.skipif(not E2E_ENABLED, reason="Live Gmail E2E tests require GMAIL_E2E_ENABLED=1")
@pytest.mark.asyncio()
async def test_live_gmail_read_and_draft_lifecycle() -> None:
    """Verify live OAuth token, profile, labels, search, and safe draft creation/deletion."""
    client = GmailClientWrapper(GmailConfig())
    status = await gmail_auth_status(client, verify_connectivity=True)
    if not status.get("authenticated"):
        pytest.skip("Live OAuth token is not authenticated yet; skipping live E2E check.")

    prof = await gmail_get_profile(client)
    assert prof["success"] is True
    assert "@" in (prof.get("email_address") or "")

    labels = await gmail_list_labels(client)
    assert labels["success"] is True
    assert labels["count"] > 0

    stats = await gmail_get_mailbox_stats(client, include_user_labels=False)
    assert stats["success"] is True

    search = await gmail_search_messages(client, query="in:inbox", max_results=5, format="metadata")
    assert search["success"] is True

    draft = await gmail_create_draft(
        client,
        to=prof["email_address"],
        subject="[MCP E2E Test Draft - Safe to Delete]",
        body_text="Automated E2E draft creation test.",
    )
    assert draft["success"] is True
    del_res = await gmail_delete_draft(client, draft_id=draft["draft_id"])
    assert del_res["success"] is True


@pytest.mark.skipif(
    not (E2E_ENABLED and E2E_SEND_ENABLED),
    reason="Live sending test is disabled by default (requires GMAIL_E2E_ENABLED=1 and GMAIL_E2E_SEND_ENABLED=1)",
)
@pytest.mark.asyncio()
async def test_live_gmail_single_send_to_allowlisted_recipient() -> None:
    """Send a single test email only when GMAIL_E2E_SEND_ENABLED=1 and GMAIL_E2E_ALLOWED_RECIPIENTS is set."""
    cfg = GmailConfig()
    if not cfg.e2e_allowed_recipients:
        pytest.skip("GMAIL_E2E_ALLOWED_RECIPIENTS is empty; refusing to send live test email.")

    client = GmailClientWrapper(cfg)
    target = cfg.e2e_allowed_recipients[0]
    res = await gmail_send_email(
        client,
        to=target,
        subject="[MCP Live Single Send Verification]",
        body_text="Single allowlisted test email.",
        confirm_send=True,
    )
    assert res["success"] is True
