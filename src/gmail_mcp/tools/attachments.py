"""Attachment listing, downloading, and MIME message attachment construction tools."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from gmail_mcp.core.gmail_client import GmailClientWrapper
from gmail_mcp.core.safety import SafetyError


async def gmail_list_attachments(
    client: GmailClientWrapper,
    message_id: str,
) -> dict[str, Any]:
    """List attachment metadata (ID, filename, MIME type, size) for a message without downloading attachment blobs."""
    try:
        resp, meta = await client.execute_api(
            "messages.get",
            {"id": message_id, "format": "full"},
            tool_name="gmail_list_attachments",
            op_class="read",
            fmt="full",
        )
        parsed = client.mime_builder.parse_gmail_payload(resp)
        attachments = parsed.get("attachments") or []
        return {
            "success": True,
            "operation": "gmail_list_attachments",
            "mutates_mailbox": False,
            "message_id": message_id,
            "thread_id": parsed.get("thread_id"),
            "attachment_count": len(attachments),
            "attachments": attachments,
            "quota_estimate": {
                "units_per_request": 60,
                "total_units": int(meta.get("quota_estimate", 60)),
            },
        }
    except Exception as exc:
        return client.format_tool_error("gmail_list_attachments", exc, mutates_mailbox=False)


async def gmail_get_attachment(
    client: GmailClientWrapper,
    message_id: str,
    attachment_id: str,
    destination_directory: str = "downloads",
    filename: str | None = None,
) -> dict[str, Any]:
    """
    Download an attachment from a message into a configured allowed directory.
    Prevents path traversal, blocks symlinks, and never executes downloaded files.
    """
    try:
        dest_dir = client.safety.resolve_safe_path(
            destination_directory, must_exist=False, allow_create_in_allowed_dir=True
        )
        dest_dir.mkdir(parents=True, exist_ok=True)

        total_units = 0
        resolved_filename = filename

        if not resolved_filename:
            msg_resp, m_meta = await client.execute_api(
                "messages.get",
                {"id": message_id, "format": "full"},
                tool_name="gmail_get_attachment",
                op_class="read",
                fmt="full",
            )
            total_units += int(m_meta.get("quota_estimate", 60))
            parsed = client.mime_builder.parse_gmail_payload(msg_resp)
            for att in parsed.get("attachments") or []:
                if att.get("attachment_id") == attachment_id and att.get("filename"):
                    resolved_filename = att["filename"]
                    break

        safe_name = Path(resolved_filename or f"attachment_{attachment_id[:8]}.bin").name
        if not safe_name or safe_name in (".", ".."):
            raise SafetyError("INVALID_ATTACHMENT_FILENAME", "Invalid attachment filename.")

        target_path = dest_dir / safe_name
        # Verify the final file path is also strictly inside allowed directories and not a symlink
        client.safety.resolve_safe_path(target_path, must_exist=False, allow_create_in_allowed_dir=True)

        att_resp, a_meta = await client.execute_api(
            "messages.attachments.get",
            {"messageId": message_id, "id": attachment_id},
            tool_name="gmail_get_attachment",
            op_class="read",
        )
        total_units += int(a_meta.get("quota_estimate", 20))

        data_b64 = att_resp.get("data") or ""
        payload_bytes = client.mime_builder.decode_base64url(data_b64)
        client.safety.validate_encoded_mime_size(len(payload_bytes))

        target_path.write_bytes(payload_bytes)
        sha256_hex = hashlib.sha256(payload_bytes).hexdigest()

        return {
            "success": True,
            "operation": "gmail_get_attachment",
            "mutates_mailbox": False,
            "message_id": message_id,
            "attachment_id": attachment_id,
            "filename": safe_name,
            "saved_path": str(target_path),
            "size_bytes": len(payload_bytes),
            "sha256": sha256_hex,
            "quota_estimate": {
                "units_per_request": 20,
                "total_units": total_units,
            },
        }
    except Exception as exc:
        return client.format_tool_error("gmail_get_attachment", exc, mutates_mailbox=False)


async def gmail_attach_files_to_message(
    client: GmailClientWrapper,
    attachments: list[str],
    *,
    to: list[str] | str | None = None,
    subject: str = "",
    body_text: str | None = None,
    body_html: str | None = None,
    draft_id: str | None = None,
    create_draft_in_gmail: bool = False,
) -> dict[str, Any]:
    """
    Construct a MIME message with validated attachments (or append attachments to an existing draft).
    Validates file existence, MIME type, path traversal rules, and encoded MIME message size (18 MB warning / 25 MB max).
    """
    try:
        if not attachments:
            raise SafetyError("MISSING_ATTACHMENTS", "At least one attachment path must be supplied.")

        if draft_id:
            # Update an existing draft by fetching its raw MIME and preserving existing content + attachments
            get_resp, g_meta = await client.execute_api(
                "drafts.get",
                {"id": draft_id, "format": "raw"},
                tool_name="gmail_attach_files_to_message",
                op_class="read",
                fmt="raw",
            )
            raw_b64 = (get_resp.get("message") or {}).get("raw") or ""
            thread_id = (get_resp.get("message") or {}).get("threadId")
            reconstructed = client.mime_builder.reconstruct_mime_from_raw(
                raw_b64,
                override_to=to,
                override_subject=subject if subject else None,
                override_body_text=body_text,
                override_body_html=body_html,
                additional_attachments=attachments,
                preserve_existing_attachments=True,
                preserve_threading_headers=bool(thread_id),
            )
            upd_body: dict[str, Any] = {"message": {"raw": reconstructed["raw"]}}
            if thread_id:
                upd_body["message"]["threadId"] = thread_id
            upd_resp, u_meta = await client.execute_api(
                "drafts.update",
                {"id": draft_id, "body": upd_body},
                tool_name="gmail_attach_files_to_message",
                op_class="write",
                attachment_bytes=reconstructed["raw_mime_size_bytes"],
            )
            total_units = int(g_meta.get("quota_estimate", 60)) + int(u_meta.get("quota_estimate", 15))
            return {
                "success": True,
                "operation": "gmail_attach_files_to_message",
                "mutates_mailbox": True,
                "draft_id": upd_resp.get("id") or draft_id,
                "attachments": reconstructed["attachments"],
                "encoded_size_bytes": reconstructed["encoded_size_bytes"],
                "warnings": reconstructed["warnings"],
                "quota_estimate": {
                    "units_per_request": 75,
                    "total_units": total_units,
                },
            }

        built = client.mime_builder.build_email_message(
            to=to or "recipient@example.com",
            subject=subject,
            body_text=body_text if (body_text is not None or body_html is not None) else "",
            body_html=body_html,
            attachments=attachments,
        )

        if create_draft_in_gmail:
            c_resp, c_meta = await client.execute_api(
                "drafts.create",
                {"body": {"message": {"raw": built["raw"]}}},
                tool_name="gmail_attach_files_to_message",
                op_class="write",
                attachment_bytes=built["raw_mime_size_bytes"],
            )
            return {
                "success": True,
                "operation": "gmail_attach_files_to_message",
                "mutates_mailbox": True,
                "draft_id": c_resp.get("id"),
                "rfc_message_id": built["rfc_message_id"],
                "attachments": built["attachments"],
                "encoded_size_bytes": built["encoded_size_bytes"],
                "warnings": built["warnings"],
                "quota_estimate": {
                    "units_per_request": 10,
                    "total_units": int(c_meta.get("quota_estimate", 10)),
                },
            }

        return {
            "success": True,
            "operation": "gmail_attach_files_to_message",
            "mutates_mailbox": False,
            "raw_mime": built["raw"],
            "rfc_message_id": built["rfc_message_id"],
            "attachments": built["attachments"],
            "encoded_size_bytes": built["encoded_size_bytes"],
            "raw_mime_size_bytes": built["raw_mime_size_bytes"],
            "warnings": built["warnings"],
            "quota_estimate": {
                "units_per_request": 0,
                "total_units": 0,
            },
        }
    except Exception as exc:
        return client.format_tool_error(
            "gmail_attach_files_to_message",
            exc,
            mutates_mailbox=bool(draft_id or create_draft_in_gmail),
        )
