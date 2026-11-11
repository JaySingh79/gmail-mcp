"""Security and operational safeguard tests (filesystem, recipients, BCC privacy, destructive ops, redaction)."""

from __future__ import annotations

from pathlib import Path

import pytest

from gmail_mcp.core.gmail_client import GmailClientWrapper
from gmail_mcp.core.safety import SafetyError
from gmail_mcp.tools.filters import gmail_create_filter
from gmail_mcp.tools.mailbox import gmail_modify_messages


def test_filesystem_path_traversal_and_outside_allowed_dir_blocked(
    gmail_client: GmailClientWrapper,
    temp_workspace: dict[str, Path],
) -> None:
    """Verify path traversal ('..') and files outside allowed directories are strictly rejected when sandbox is active."""
    with pytest.raises(SafetyError) as exc_traversal:
        gmail_client.safety.resolve_safe_path("../../.env", must_exist=False)
    assert exc_traversal.value.error_code == "PATH_TRAVERSAL_BLOCKED"

    # When sandbox is strictly enforced, outside paths are rejected
    gmail_client.config.allow_any_attachment_dir = False
    outside_file = temp_workspace["root"] / "secret_outside.txt"
    outside_file.write_text("do not attach", encoding="utf-8")
    with pytest.raises(SafetyError) as exc_outside:
        gmail_client.safety.validate_attachment_file(outside_file)
    assert exc_outside.value.error_code == "PATH_OUTSIDE_ALLOWED_DIRS"

    with pytest.raises(SafetyError) as exc_missing:
        gmail_client.safety.validate_attachment_file(temp_workspace["attachments"] / "nonexistent.pdf")
    assert exc_missing.value.error_code == "ATTACHMENT_FILE_NOT_FOUND"


def test_attachment_allowed_from_any_directory_by_default(
    gmail_client: GmailClientWrapper,
    temp_workspace: dict[str, Path],
) -> None:
    """Verify attachments can be attached from any directory by default."""
    assert gmail_client.config.allow_any_attachment_dir is True
    any_dir_file = temp_workspace["root"] / "arbitrary_custom_folder" / "external_resume.pdf"
    any_dir_file.parent.mkdir(parents=True, exist_ok=True)
    any_dir_file.write_text("dummy resume content", encoding="utf-8")

    validated = gmail_client.safety.validate_attachment_file(any_dir_file)
    assert validated["filename"] == "external_resume.pdf"
    assert validated["size_bytes"] == len("dummy resume content")


def test_attachment_size_ceiling_enforced(
    gmail_client: GmailClientWrapper,
    temp_workspace: dict[str, Path],
) -> None:
    """Verify attachments exceeding the 25 MB limit are rejected and never silently removed."""
    gmail_client.config.attachment_max_size_mb = 0.001  # ~1 KB for fast test
    large_file = temp_workspace["attachments"] / "oversized.bin"
    large_file.write_bytes(b"X" * 4096)

    with pytest.raises(SafetyError) as exc_size:
        gmail_client.safety.validate_attachment_file(large_file)
    assert exc_size.value.error_code == "ATTACHMENT_SIZE_EXCEEDED"


def test_recipient_validation_deduplication_and_bcc_privacy(
    gmail_client: GmailClientWrapper,
) -> None:
    """Verify invalid addresses are rejected, duplicates are removed, and BCC addresses are hidden in summaries."""
    with pytest.raises(SafetyError) as exc_invalid:
        gmail_client.safety.validate_and_deduplicate_recipients("not-an-email-address")
    assert exc_invalid.value.error_code == "INVALID_RECIPIENT"

    res = gmail_client.safety.validate_and_deduplicate_recipients(
        to=["Alice <alice@example.com>", "alice@example.com"],
        cc=["Bob <bob@example.com>", "alice@example.com"],
        bcc=["hidden1@example.com", "hidden2@example.com"],
    )
    assert len(res["to"]) == 1
    assert len(res["cc"]) == 1
    assert len(res["bcc"]) == 2
    assert len(res["duplicates_removed"]) == 2
    assert "hidden1@example.com" not in str(res["public_summary"])
    assert res["public_summary"]["bcc_count"] == 2


@pytest.mark.asyncio()
async def test_destructive_permanent_delete_and_forward_filter_require_confirmation(
    gmail_client: GmailClientWrapper,
) -> None:
    """Verify permanent deletion and filter forwarding are blocked without explicit confirmation."""
    del_res = await gmail_modify_messages(
        gmail_client,
        action="permanent_delete",
        message_ids=["msg_1"],
    )
    assert del_res["success"] is False
    assert del_res["error_code"] == "PERMANENT_DELETE_CONFIRMATION_REQUIRED"

    fwd_res = await gmail_create_filter(
        gmail_client,
        criteria={"from": "alerts@example.com"},
        actions={"forward": "external@example.com"},
        confirm_forward=False,
    )
    assert fwd_res["success"] is False
    assert fwd_res["error_code"] == "FORWARD_FILTER_CONFIRMATION_REQUIRED"


def test_log_redaction_strips_oauth_tokens(gmail_client: GmailClientWrapper) -> None:
    """Verify OAuth access tokens, refresh tokens, and client secrets are redacted by SafetyGuard."""
    raw_log = 'Token error: ya29.a0AfH6SMBx123456 and "refresh_token": "1//0gabcdef12345"'
    redacted = gmail_client.safety.redact_text(raw_log)
    assert "ya29." not in redacted
    assert "1//0gabcdef" not in redacted
    assert "[REDACTED]" in redacted
