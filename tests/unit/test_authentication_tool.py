"""Unit tests for gmail_authenticate tool and model-actionable authentication error guidance."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from google.oauth2.credentials import Credentials

from gmail_mcp.auth.credentials import DEFAULT_SCOPES
from gmail_mcp.auth.token_manager import TokenManager
from gmail_mcp.config import GmailConfig
from gmail_mcp.core.gmail_client import GmailClientWrapper
from gmail_mcp.storage.database import DatabaseManager
from gmail_mcp.tools.account import (
    gmail_auth_status,
    gmail_authenticate,
    gmail_get_profile,
)
from tests.conftest import MockGmailBackend, make_http_error


@pytest.fixture()
def unauth_client(temp_workspace: dict[str, Path], mock_backend: MockGmailBackend) -> GmailClientWrapper:
    """Create a GmailClientWrapper without any valid token.json."""
    root = temp_workspace["root"]
    config = GmailConfig(
        workspace_root=root,
        credentials_path=root / "credentials.json",
        token_path=root / "non_existent_token.json",
        db_path=root / "test_unauth.db",
        allowed_dirs=[temp_workspace["attachments"]],
        send_interval_seconds=0.0,
    )
    db = DatabaseManager(":memory:")
    token_mgr = TokenManager(config)
    return GmailClientWrapper(
        config=config,
        db=db,
        token_manager=token_mgr,
        custom_dispatcher=mock_backend.dispatch,
    )


@pytest.mark.asyncio()
async def test_unauthenticated_tool_call_returns_suggested_authenticate_tool(
    unauth_client: GmailClientWrapper,
) -> None:
    """Calling any operational tool without tokens returns AUTH_NOT_CONFIGURED and points model to gmail_authenticate."""
    res = await gmail_get_profile(unauth_client)
    assert res["success"] is False
    assert res["error_code"] == "AUTH_NOT_CONFIGURED"
    assert res["action_required"] == "authenticate"
    assert res["suggested_tool"] == "gmail_authenticate"
    assert res["suggested_arguments"] == {"force_reauth": False}
    assert "gmail_authenticate" in res["message"]


@pytest.mark.asyncio()
async def test_gmail_auth_status_unauthenticated_includes_suggestion(
    unauth_client: GmailClientWrapper,
) -> None:
    """gmail_auth_status reports authenticated=False and includes actionable tool suggestion."""
    res = await gmail_auth_status(unauth_client, verify_connectivity=False)
    assert res["success"] is True
    assert res["authenticated"] is False
    assert res["status_code"] == "TOKEN_MISSING"
    assert res["action_required"] == "authenticate"
    assert res["suggested_tool"] == "gmail_authenticate"
    assert res["suggested_arguments"] == {"force_reauth": False}


@pytest.mark.asyncio()
async def test_gmail_authenticate_already_authenticated(
    gmail_client: GmailClientWrapper,
) -> None:
    """When valid tokens exist, gmail_authenticate returns immediately without launching browser."""
    res = await gmail_authenticate(gmail_client, force_reauth=False)
    assert res["success"] is True
    assert res["operation"] == "gmail_authenticate"
    assert res["status"] == "already_authenticated"
    assert res["reused_existing_token"] is True
    assert res["email_address"] == "testuser@gmail.com"
    assert res["history_id"] == "1000"
    assert len(res["scopes"]) > 0


@pytest.mark.asyncio()
async def test_gmail_authenticate_credentials_file_missing(
    temp_workspace: dict[str, Path],
    mock_backend: MockGmailBackend,
) -> None:
    """When credentials JSON file does not exist, clean error with resolution steps is returned."""
    empty_dir = temp_workspace["root"] / "empty_dir"
    empty_dir.mkdir(parents=True, exist_ok=True)
    config = GmailConfig(
        workspace_root=empty_dir,
        credentials_path=empty_dir / "missing_creds.json",
        token_path=empty_dir / "token.json",
        db_path=empty_dir / "test.db",
    )
    token_mgr = TokenManager(config)
    client = GmailClientWrapper(
        config=config,
        db=DatabaseManager(":memory:"),
        token_manager=token_mgr,
        custom_dispatcher=mock_backend.dispatch,
    )

    res = await gmail_authenticate(client, force_reauth=True)
    assert res["success"] is False
    assert res["error_code"] == "CREDENTIALS_FILE_MISSING"
    assert "OAuth client secrets file not found" in res["message"]
    assert len(res["resolution_steps"]) >= 2


@pytest.mark.asyncio()
async def test_gmail_authenticate_flow_success(
    gmail_client: GmailClientWrapper,
) -> None:
    """Interactive login flow executes, saves credentials, invalidates caches, and returns profile."""
    # Ensure client credentials file exists so check passes
    gmail_client.config.credentials_path.write_text('{"installed": {}}', encoding="utf-8")

    mock_login_result = {
        "success": True,
        "reused_existing_token": False,
        "email_address": "testuser@gmail.com",
        "scopes": list(DEFAULT_SCOPES),
    }

    with patch("gmail_mcp.tools.account.OAuthFlowManager.run_interactive_login", return_value=mock_login_result):
        res = await gmail_authenticate(gmail_client, force_reauth=True)

    assert res["success"] is True
    assert res["operation"] == "gmail_authenticate"
    assert res["status"] == "authenticated"
    assert res["email_address"] == "testuser@gmail.com"
    assert res["reused_existing_token"] is False


@pytest.mark.asyncio()
async def test_gmail_authenticate_timeout(
    gmail_client: GmailClientWrapper,
) -> None:
    """When user browser consent times out, structured AUTH_TIMEOUT error is returned."""
    gmail_client.config.credentials_path.write_text('{"installed": {}}', encoding="utf-8")

    def _slow_login(*args: Any, **kwargs: Any) -> dict[str, Any]:
        import time
        time.sleep(1.0)
        return {}

    with patch("gmail_mcp.tools.account.OAuthFlowManager.run_interactive_login", side_effect=_slow_login):
        res = await gmail_authenticate(gmail_client, force_reauth=True, timeout_seconds=0.05)  # type: ignore[arg-type]

    assert res["success"] is False
    assert res["error_code"] == "AUTH_TIMEOUT"
    assert "timed out" in res["message"]
    assert len(res["resolution_steps"]) >= 2


@pytest.mark.asyncio()
async def test_insufficient_scope_error_suggests_reauth(
    gmail_client: GmailClientWrapper,
    mock_backend: MockGmailBackend,
) -> None:
    """When an API call fails with 403 insufficientPermissions, response advises gmail_authenticate with force_reauth=True."""
    mock_backend.inject_fault(
        "users.getProfile",
        make_http_error(403, "insufficientPermissions", "Request had insufficient authentication scopes."),
    )

    res = await gmail_get_profile(gmail_client)
    assert res["success"] is False
    assert res["error_code"] == "INSUFFICIENT_SCOPE"
    assert res["action_required"] == "reauthenticate"
    assert res["suggested_tool"] == "gmail_authenticate"
    assert res["suggested_arguments"] == {"force_reauth": True}


@pytest.mark.asyncio
async def test_gmail_authenticate_handles_keyboard_interrupt(temp_workspace: dict[str, Path], monkeypatch: pytest.MonkeyPatch):
    """Verify that user cancelling OAuth flow via Ctrl+C produces clean error report."""
    from gmail_mcp.auth.oauth import OAuthFlowManager
    import json

    root = temp_workspace["root"]
    config = GmailConfig(
        workspace_root=root,
        db_path=root / "test.db",
        token_path=root / "token.json",
        credentials_path=root / "credentials.json",
    )
    client = GmailClientWrapper(config)
    client.config.credentials_path.write_text(json.dumps({"installed": {}}), encoding="utf-8")

    def _mock_interrupt(*args, **kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(OAuthFlowManager, "run_interactive_login", _mock_interrupt)

    res = await gmail_authenticate(client, force_reauth=True, timeout_seconds=10)
    assert res["success"] is False
    assert res["error_code"] == "AUTH_CANCELLED"
    assert "cancelled" in res["message"].lower()


def test_oauth_main_handles_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch):
    """Verify CLI main cleanly catches KeyboardInterrupt and returns exit code 130."""
    from gmail_mcp.auth.oauth import OAuthFlowManager, main as oauth_main
    import sys

    monkeypatch.setattr(sys, "argv", ["oauth.py", "--login"])

    def _mock_interrupt(*args, **kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(OAuthFlowManager, "run_interactive_login", _mock_interrupt)
    code = oauth_main()
    assert code == 130

