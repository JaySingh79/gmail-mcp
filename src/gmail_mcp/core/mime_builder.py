"""RFC-compliant MIME message builder, parser, and reconstructor for Gmail REST API v1."""

from __future__ import annotations

import base64
import mimetypes
import uuid
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import Any

from gmail_mcp.core.safety import SafetyError, SafetyGuard

# Ensure common office/document MIME types are consistently registered across OS environments
mimetypes.add_type("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx")
mimetypes.add_type("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx")
mimetypes.add_type("application/pdf", ".pdf")
mimetypes.add_type("text/csv", ".csv")
mimetypes.add_type("text/plain", ".txt")
mimetypes.add_type("image/png", ".png")
mimetypes.add_type("image/jpeg", ".jpg")
mimetypes.add_type("image/jpeg", ".jpeg")
mimetypes.add_type("application/zip", ".zip")


class MimeBuilder:
    """Builds, parses, duplicates, and reconstructs MIME messages for Gmail REST API."""

    def __init__(self, safety: SafetyGuard) -> None:
        self.safety = safety

    @staticmethod
    def encode_base64url(data: bytes) -> str:
        """Encode bytes into unpadded or standard URL-safe base64 string expected by Gmail API."""
        return base64.urlsafe_b64encode(data).decode("ascii")

    @staticmethod
    def decode_base64url(data: str) -> bytes:
        """Decode Gmail URL-safe base64 string with automatic padding correction."""
        cleaned = data.strip()
        padding = (4 - len(cleaned) % 4) % 4
        return base64.urlsafe_b64decode(cleaned + ("=" * padding))

    @staticmethod
    def detect_mime_type(file_path: str | Path) -> tuple[str, str]:
        """Return (maintype, subtype) for a file path."""
        mime_type, _ = mimetypes.guess_type(str(file_path))
        if not mime_type or "/" not in mime_type:
            return "application", "octet-stream"
        maintype, subtype = mime_type.split("/", 1)
        return maintype, subtype

    def build_email_message(
        self,
        *,
        to: list[str] | str,
        subject: str,
        body_text: str | None = None,
        body_html: str | None = None,
        cc: list[str] | str | None = None,
        bcc: list[str] | str | None = None,
        reply_to: str | None = None,
        from_address: str | None = None,
        in_reply_to: str | None = None,
        references: str | None = None,
        custom_headers: dict[str, str] | None = None,
        attachments: list[str | Path] | None = None,
        extra_attachment_blobs: list[dict[str, Any]] | None = None,
        rfc_message_id: str | None = None,
    ) -> dict[str, Any]:
        """Construct an EmailMessage, attach files, validate size, and return raw base64url + metadata."""
        if body_text is None and body_html is None:
            raise SafetyError("EMPTY_MESSAGE_BODY", "Either body_text or body_html must be provided.")

        recip_info = self.safety.validate_and_deduplicate_recipients(to=to, cc=cc, bcc=bcc)

        msg = EmailMessage(policy=policy.default)
        if from_address:
            msg["From"] = from_address
        if recip_info["to"]:
            msg["To"] = ", ".join(recip_info["to"])
        if recip_info["cc"]:
            msg["Cc"] = ", ".join(recip_info["cc"])
        if recip_info["bcc"]:
            msg["Bcc"] = ", ".join(recip_info["bcc"])
        if reply_to:
            self.safety.extract_email_address(reply_to)
            msg["Reply-To"] = reply_to

        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        generated_msgid = rfc_message_id or make_msgid(idstring=uuid.uuid4().hex[:12], domain="gmail-mcp.local")
        msg["Message-ID"] = generated_msgid

        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
        if references:
            msg["References"] = references

        if custom_headers:
            forbidden = {"from", "to", "cc", "bcc", "subject", "date", "message-id"}
            for hdr_name, hdr_val in custom_headers.items():
                if hdr_name.lower() not in forbidden and hdr_val:
                    msg[hdr_name] = str(hdr_val)

        # Set body content (plaintext, HTML, or multipart/alternative)
        if body_text is not None and body_html is not None:
            msg.set_content(body_text, subtype="plain", charset="utf-8")
            msg.add_alternative(body_html, subtype="html", charset="utf-8")
        elif body_html is not None:
            msg.set_content(body_html, subtype="html", charset="utf-8")
        else:
            msg.set_content(body_text or "", subtype="plain", charset="utf-8")

        warnings: list[str] = []
        attached_meta: list[dict[str, Any]] = []

        # Preserve pre-existing attachment blobs (e.g. during draft update/duplication/forward)
        for blob in extra_attachment_blobs or []:
            maintype, subtype = (blob.get("mime_type") or "application/octet-stream").split("/", 1)
            payload_bytes: bytes = blob["data"]
            filename = blob.get("filename") or "attachment.bin"
            msg.add_attachment(
                payload_bytes,
                maintype=maintype,
                subtype=subtype,
                filename=filename,
            )
            attached_meta.append(
                {
                    "filename": filename,
                    "mime_type": f"{maintype}/{subtype}",
                    "size_bytes": len(payload_bytes),
                }
            )

        # Attach filesystem files
        for att_path in attachments or []:
            validated = self.safety.validate_attachment_file(att_path)
            if validated["warning"]:
                warnings.append(validated["warning"])
            resolved: Path = validated["path"]
            maintype, subtype = self.detect_mime_type(resolved)
            data = resolved.read_bytes()
            msg.add_attachment(
                data,
                maintype=maintype,
                subtype=subtype,
                filename=resolved.name,
            )
            attached_meta.append(
                {
                    "filename": resolved.name,
                    "mime_type": f"{maintype}/{subtype}",
                    "size_bytes": len(data),
                    "path": str(resolved),
                }
            )

        raw_bytes = msg.as_bytes()
        raw_b64 = self.encode_base64url(raw_bytes)
        encoded_warning = self.safety.validate_encoded_mime_size(len(raw_b64.encode("ascii")))
        if encoded_warning:
            warnings.append(encoded_warning)

        return {
            "raw": raw_b64,
            "rfc_message_id": generated_msgid,
            "encoded_size_bytes": len(raw_b64),
            "raw_mime_size_bytes": len(raw_bytes),
            "recipients": recip_info,
            "attachments": attached_meta,
            "warnings": warnings,
            "email_message": msg,
        }

    def parse_raw_mime(self, raw_b64: str) -> dict[str, Any]:
        """Decode base64url raw MIME message and extract headers, bodies, and attachment blobs."""
        raw_bytes = self.decode_base64url(raw_b64)
        msg = BytesParser(policy=policy.default).parsebytes(raw_bytes)

        headers: dict[str, str] = {}
        for k, v in msg.items():
            headers[k] = str(v)

        body_text: str | None = None
        body_html: str | None = None
        attachments: list[dict[str, Any]] = []

        if msg.is_multipart():
            for part in msg.walk():
                if part.is_multipart():
                    continue
                content_disposition = part.get_content_disposition()
                filename = part.get_filename()
                content_type = part.get_content_type()
                if content_disposition == "attachment" or filename:
                    payload = part.get_payload(decode=True) or b""
                    attachments.append(
                        {
                            "filename": filename or "attachment.bin",
                            "mime_type": content_type,
                            "size_bytes": len(payload),
                            "data": payload,
                        }
                    )
                elif content_type == "text/plain" and body_text is None:
                    payload = part.get_payload(decode=True)
                    if payload is not None:
                        charset = part.get_content_charset() or "utf-8"
                        body_text = payload.decode(charset, errors="replace")
                elif content_type == "text/html" and body_html is None:
                    payload = part.get_payload(decode=True)
                    if payload is not None:
                        charset = part.get_content_charset() or "utf-8"
                        body_html = payload.decode(charset, errors="replace")
        else:
            content_type = msg.get_content_type()
            payload = msg.get_payload(decode=True) or b""
            charset = msg.get_content_charset() or "utf-8"
            decoded = payload.decode(charset, errors="replace")
            if content_type == "text/html":
                body_html = decoded
            else:
                body_text = decoded

        return {
            "headers": headers,
            "subject": headers.get("Subject", ""),
            "from": headers.get("From", ""),
            "to": [a.strip() for a in headers.get("To", "").split(",") if a.strip()],
            "cc": [a.strip() for a in headers.get("Cc", "").split(",") if a.strip()],
            "bcc": [a.strip() for a in headers.get("Bcc", "").split(",") if a.strip()],
            "reply_to": headers.get("Reply-To"),
            "in_reply_to": headers.get("In-Reply-To"),
            "references": headers.get("References"),
            "body_text": body_text,
            "body_html": body_html,
            "attachments": attachments,
            "email_message": msg,
        }

    def reconstruct_mime_from_raw(
        self,
        raw_b64: str,
        *,
        override_to: list[str] | str | None = None,
        override_cc: list[str] | str | None = None,
        override_bcc: list[str] | str | None = None,
        override_subject: str | None = None,
        override_body_text: str | None = None,
        override_body_html: str | None = None,
        additional_attachments: list[str | Path] | None = None,
        preserve_existing_attachments: bool = True,
        preserve_threading_headers: bool = False,
    ) -> dict[str, Any]:
        """Reconstruct a new MIME message from an existing raw MIME draft/message without losing attachments."""
        parsed = self.parse_raw_mime(raw_b64)
        target_to = override_to if override_to is not None else parsed["to"]
        target_cc = override_cc if override_cc is not None else parsed["cc"]
        target_bcc = override_bcc if override_bcc is not None else parsed["bcc"]
        target_subject = override_subject if override_subject is not None else parsed["subject"]
        target_text = override_body_text if override_body_text is not None else parsed["body_text"]
        target_html = override_body_html if override_body_html is not None else parsed["body_html"]

        if target_text is None and target_html is None:
            target_text = ""

        # Drafts may legitimately be saved before a recipient is added; if no recipient exists on draft,
        # build the EmailMessage directly when target_to is empty.
        has_any_recipient = bool(target_to or target_cc or target_bcc)
        existing_blobs = parsed["attachments"] if preserve_existing_attachments else []

        if has_any_recipient:
            return self.build_email_message(
                to=target_to,
                cc=target_cc,
                bcc=target_bcc,
                reply_to=parsed["reply_to"],
                subject=target_subject,
                body_text=target_text,
                body_html=target_html,
                in_reply_to=parsed["in_reply_to"] if preserve_threading_headers else None,
                references=parsed["references"] if preserve_threading_headers else None,
                attachments=additional_attachments,
                extra_attachment_blobs=existing_blobs,
            )

        # Recipient-less draft reconstruction
        msg = EmailMessage(policy=policy.default)
        msg["Subject"] = target_subject
        msg["Date"] = formatdate(localtime=True)
        generated_msgid = make_msgid(idstring=uuid.uuid4().hex[:12], domain="gmail-mcp.local")
        msg["Message-ID"] = generated_msgid
        if preserve_threading_headers:
            if parsed["in_reply_to"]:
                msg["In-Reply-To"] = parsed["in_reply_to"]
            if parsed["references"]:
                msg["References"] = parsed["references"]

        if target_text is not None and target_html is not None:
            msg.set_content(target_text, subtype="plain", charset="utf-8")
            msg.add_alternative(target_html, subtype="html", charset="utf-8")
        elif target_html is not None:
            msg.set_content(target_html, subtype="html", charset="utf-8")
        else:
            msg.set_content(target_text or "", subtype="plain", charset="utf-8")

        attached_meta: list[dict[str, Any]] = []
        warnings: list[str] = []
        for blob in existing_blobs:
            maintype, subtype = (blob.get("mime_type") or "application/octet-stream").split("/", 1)
            msg.add_attachment(
                blob["data"],
                maintype=maintype,
                subtype=subtype,
                filename=blob["filename"],
            )
            attached_meta.append(
                {
                    "filename": blob["filename"],
                    "mime_type": f"{maintype}/{subtype}",
                    "size_bytes": len(blob["data"]),
                }
            )

        for att_path in additional_attachments or []:
            validated = self.safety.validate_attachment_file(att_path)
            if validated["warning"]:
                warnings.append(validated["warning"])
            resolved: Path = validated["path"]
            maintype, subtype = self.detect_mime_type(resolved)
            data = resolved.read_bytes()
            msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=resolved.name)
            attached_meta.append(
                {
                    "filename": resolved.name,
                    "mime_type": f"{maintype}/{subtype}",
                    "size_bytes": len(data),
                    "path": str(resolved),
                }
            )

        raw_bytes = msg.as_bytes()
        raw_b64_out = self.encode_base64url(raw_bytes)
        encoded_warning = self.safety.validate_encoded_mime_size(len(raw_b64_out.encode("ascii")))
        if encoded_warning:
            warnings.append(encoded_warning)

        return {
            "raw": raw_b64_out,
            "rfc_message_id": generated_msgid,
            "encoded_size_bytes": len(raw_b64_out),
            "raw_mime_size_bytes": len(raw_bytes),
            "recipients": {"to": [], "cc": [], "bcc": [], "bcc_count": 0, "total_count": 0},
            "attachments": attached_meta,
            "warnings": warnings,
            "email_message": msg,
        }

    @staticmethod
    def build_reply_headers(source_headers: dict[str, str]) -> dict[str, str]:
        """Construct RFC-2822 threading headers (`Subject`, `In-Reply-To`, `References`) from source headers."""
        ci_headers = {k.lower(): v for k, v in source_headers.items()}
        orig_subject = ci_headers.get("subject", "").strip()
        if orig_subject.lower().startswith("re:"):
            reply_subject = orig_subject
        else:
            reply_subject = f"Re: {orig_subject}" if orig_subject else "Re:"

        parent_msgid = ci_headers.get("message-id", "").strip()
        orig_refs = ci_headers.get("references", "").strip()
        if parent_msgid:
            if orig_refs:
                references = f"{orig_refs} {parent_msgid}" if parent_msgid not in orig_refs else orig_refs
            else:
                references = parent_msgid
        else:
            references = orig_refs

        reply_target = ci_headers.get("reply-to") or ci_headers.get("from", "")
        return {
            "subject": reply_subject,
            "in_reply_to": parent_msgid,
            "references": references,
            "reply_to_address": reply_target,
            "original_to": ci_headers.get("to", ""),
            "original_cc": ci_headers.get("cc", ""),
        }

    @classmethod
    def parse_gmail_payload(cls, message_resource: dict[str, Any]) -> dict[str, Any]:
        """Parse a Gmail REST API Message resource (METADATA, MINIMAL, FULL, or RAW) into structured fields."""
        payload = message_resource.get("payload") or {}
        raw_headers = payload.get("headers") or []
        headers: dict[str, str] = {}
        for h in raw_headers:
            name = h.get("name")
            val = h.get("value")
            if name and val is not None:
                headers[name] = val

        ci_headers = {k.lower(): v for k, v in headers.items()}
        body_text: str | None = None
        body_html: str | None = None
        attachments: list[dict[str, Any]] = []

        def _walk_part(part: dict[str, Any]) -> None:
            nonlocal body_text, body_html
            mime_type = part.get("mimeType", "")
            filename = part.get("filename")
            body = part.get("body") or {}
            att_id = body.get("attachmentId")
            data_b64 = body.get("data")
            size = int(body.get("size") or 0)

            if filename or att_id:
                attachments.append(
                    {
                        "part_id": part.get("partId"),
                        "filename": filename or "attachment.bin",
                        "mime_type": mime_type or "application/octet-stream",
                        "attachment_id": att_id,
                        "size_bytes": size,
                    }
                )
            elif mime_type == "text/plain" and data_b64 and body_text is None:
                body_text = cls.decode_base64url(data_b64).decode("utf-8", errors="replace")
            elif mime_type == "text/html" and data_b64 and body_html is None:
                body_html = cls.decode_base64url(data_b64).decode("utf-8", errors="replace")

            for subpart in part.get("parts") or []:
                _walk_part(subpart)

        if payload:
            _walk_part(payload)

        return {
            "id": message_resource.get("id"),
            "thread_id": message_resource.get("threadId"),
            "label_ids": message_resource.get("labelIds") or [],
            "snippet": message_resource.get("snippet"),
            "history_id": message_resource.get("historyId"),
            "internal_date": message_resource.get("internalDate"),
            "size_estimate": message_resource.get("sizeEstimate"),
            "headers": headers,
            "subject": ci_headers.get("subject", ""),
            "from": ci_headers.get("from", ""),
            "to": ci_headers.get("to", ""),
            "cc": ci_headers.get("cc", ""),
            "date": ci_headers.get("date", ""),
            "rfc_message_id": ci_headers.get("message-id", ""),
            "in_reply_to": ci_headers.get("in-reply-to", ""),
            "references": ci_headers.get("references", ""),
            "body_text": body_text,
            "body_html": body_html,
            "attachments": attachments,
            "has_attachments": len(attachments) > 0,
            "is_unread": "UNREAD" in (message_resource.get("labelIds") or []),
        }
