"""Shared pytest fixtures and stateful MockGmailBackend for deterministic unit, security, and stress tests."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from google.oauth2.credentials import Credentials
from googleapiclient.errors import HttpError

from gmail_mcp.auth.credentials import DEFAULT_SCOPES
from gmail_mcp.auth.token_manager import TokenManager
from gmail_mcp.config import GmailConfig
from gmail_mcp.core.gmail_client import GmailClientWrapper
from gmail_mcp.storage.database import DatabaseManager


def make_http_error(status: int, reason: str, message: str, retry_after: str | None = None) -> HttpError:
    """Construct a realistic googleapiclient HttpError for error-classification testing."""
    resp = MagicMock()
    resp.status = status
    resp.reason = message
    headers = {"retry-after": retry_after} if retry_after else {}
    resp.get.side_effect = lambda k, default=None: headers.get(k.lower(), default)
    body = json.dumps(
        {
            "error": {
                "code": status,
                "message": message,
                "errors": [{"domain": "global", "reason": reason, "message": message}],
            }
        }
    ).encode("utf-8")
    return HttpError(resp=resp, content=body)


class MockGmailBackend:
    """
    Stateful in-memory Gmail REST API v1 simulator supporting messages, threads, drafts,
    attachments, labels, filters, history, watch, and fault injection.
    """

    def __init__(self, email_address: str = "testuser@gmail.com") -> None:
        self.email_address = email_address
        self.history_id = 1000
        self.messages: dict[str, dict[str, Any]] = {}
        self.threads: dict[str, list[str]] = {}
        self.drafts: dict[str, dict[str, Any]] = {}
        self.attachments: dict[tuple[str, str], bytes] = {}
        self.labels: dict[str, dict[str, Any]] = {
            "INBOX": {"id": "INBOX", "name": "INBOX", "type": "system", "messagesTotal": 0, "messagesUnread": 0, "threadsTotal": 0, "threadsUnread": 0},
            "UNREAD": {"id": "UNREAD", "name": "UNREAD", "type": "system", "messagesTotal": 0, "messagesUnread": 0, "threadsTotal": 0, "threadsUnread": 0},
            "SENT": {"id": "SENT", "name": "SENT", "type": "system", "messagesTotal": 0, "messagesUnread": 0, "threadsTotal": 0, "threadsUnread": 0},
            "DRAFT": {"id": "DRAFT", "name": "DRAFT", "type": "system", "messagesTotal": 0, "messagesUnread": 0, "threadsTotal": 0, "threadsUnread": 0},
            "SPAM": {"id": "SPAM", "name": "SPAM", "type": "system", "messagesTotal": 0, "messagesUnread": 0, "threadsTotal": 0, "threadsUnread": 0},
            "TRASH": {"id": "TRASH", "name": "TRASH", "type": "system", "messagesTotal": 0, "messagesUnread": 0, "threadsTotal": 0, "threadsUnread": 0},
            "STARRED": {"id": "STARRED", "name": "STARRED", "type": "system", "messagesTotal": 0, "messagesUnread": 0, "threadsTotal": 0, "threadsUnread": 0},
            "IMPORTANT": {"id": "IMPORTANT", "name": "IMPORTANT", "type": "system", "messagesTotal": 0, "messagesUnread": 0, "threadsTotal": 0, "threadsUnread": 0},
        }
        self.filters: dict[str, dict[str, Any]] = {}
        self.history_records: list[dict[str, Any]] = []
        self.expired_history_ids: set[str] = set()
        self.vacation_settings: dict[str, Any] = {
            "enableAutoReply": False,
            "responseSubject": "",
            "responseBodyPlainText": "",
        }
        self.send_as_aliases: list[dict[str, Any]] = [
            {
                "sendAsEmail": email_address,
                "displayName": "Test User",
                "isPrimary": True,
                "isDefault": True,
                "verificationStatus": "accepted",
            },
            {
                "sendAsEmail": "alias@gmail.com",
                "displayName": "Verified Alias",
                "isPrimary": False,
                "isDefault": False,
                "verificationStatus": "accepted",
            },
        ]
        self.call_log: list[tuple[str, dict[str, Any]]] = []
        self.fault_injector: list[tuple[str, Exception]] = []
        self._msg_seq = 1
        self._draft_seq = 1
        self._label_seq = 1
        self._filter_seq = 1

    def inject_fault(self, method: str, exc: Exception) -> None:
        """Queue a one-shot exception to raise on the next call to `method`."""
        self.fault_injector.append((method, exc))

    def seed_message(
        self,
        *,
        msg_id: str | None = None,
        thread_id: str | None = None,
        sender: str = "john@example.com",
        to: str = "testuser@gmail.com",
        cc: str = "",
        subject: str = "Hello World",
        body_text: str = "Plaintext message body",
        body_html: str | None = "<p>HTML message body</p>",
        label_ids: list[str] | None = None,
        rfc_message_id: str | None = None,
        references: str = "",
        in_reply_to: str = "",
        attachments: list[tuple[str, str, bytes]] | None = None,
    ) -> dict[str, Any]:
        """Seed a realistic message into the mock mailbox."""
        mid = msg_id or f"msg_{self._msg_seq}"
        self._msg_seq += 1
        tid = thread_id or f"thread_{mid}"
        rfc_id = rfc_message_id or f"<{mid}@example.com>"
        lbls = list(label_ids if label_ids is not None else ["INBOX", "UNREAD"])

        em = EmailMessage(policy=policy.default)
        em["From"] = sender
        em["To"] = to
        if cc:
            em["Cc"] = cc
        em["Subject"] = subject
        em["Date"] = "Mon, 02 Nov 2026 10:00:00 +0530"
        em["Message-ID"] = rfc_id
        if in_reply_to:
            em["In-Reply-To"] = in_reply_to
        if references:
            em["References"] = references

        if body_text and body_html:
            em.set_content(body_text, subtype="plain", charset="utf-8")
            em.add_alternative(body_html, subtype="html", charset="utf-8")
        elif body_html:
            em.set_content(body_html, subtype="html", charset="utf-8")
        else:
            em.set_content(body_text, subtype="plain", charset="utf-8")

        parts: list[dict[str, Any]] = [
            {
                "partId": "0",
                "mimeType": "text/plain",
                "filename": "",
                "body": {
                    "size": len(body_text.encode("utf-8")),
                    "data": base64.urlsafe_b64encode(body_text.encode("utf-8")).decode("ascii"),
                },
            }
        ]
        if body_html:
            parts.append(
                {
                    "partId": "1",
                    "mimeType": "text/html",
                    "filename": "",
                    "body": {
                        "size": len(body_html.encode("utf-8")),
                        "data": base64.urlsafe_b64encode(body_html.encode("utf-8")).decode("ascii"),
                    },
                }
            )

        for idx, (fname, mtype, data) in enumerate(attachments or [], start=2):
            maintype, subtype = mtype.split("/", 1)
            em.add_attachment(data, maintype=maintype, subtype=subtype, filename=fname)
            att_id = f"att_{mid}_{idx}"
            self.attachments[(mid, att_id)] = data
            parts.append(
                {
                    "partId": str(idx),
                    "mimeType": mtype,
                    "filename": fname,
                    "body": {"attachmentId": att_id, "size": len(data)},
                }
            )

        raw_b64 = base64.urlsafe_b64encode(em.as_bytes()).decode("ascii")
        headers_list = [
            {"name": "From", "value": sender},
            {"name": "To", "value": to},
            {"name": "Subject", "value": subject},
            {"name": "Date", "value": "Mon, 02 Nov 2026 10:00:00 +0530"},
            {"name": "Message-ID", "value": rfc_id},
        ]
        if cc:
            headers_list.append({"name": "Cc", "value": cc})
        if in_reply_to:
            headers_list.append({"name": "In-Reply-To", "value": in_reply_to})
        if references:
            headers_list.append({"name": "References", "value": references})

        record = {
            "id": mid,
            "threadId": tid,
            "labelIds": lbls,
            "snippet": body_text[:100],
            "historyId": str(self.history_id),
            "internalDate": "1793600000000",
            "sizeEstimate": len(raw_b64),
            "raw": raw_b64,
            "payload": {
                "mimeType": "multipart/mixed" if attachments else "multipart/alternative",
                "headers": headers_list,
                "parts": parts,
            },
        }
        self.messages[mid] = record
        self.threads.setdefault(tid, []).append(mid)
        return record

    def dispatch(self, method: str, params: dict[str, Any]) -> Any:
        """Execute a simulated Gmail REST API v1 request."""
        self.call_log.append((method, params))

        for idx, (fault_method, exc) in enumerate(self.fault_injector):
            if fault_method in (method, "*"):
                self.fault_injector.pop(idx)
                raise exc

        if method == "users.getProfile":
            return {
                "emailAddress": self.email_address,
                "messagesTotal": len(self.messages),
                "threadsTotal": len(self.threads),
                "historyId": str(self.history_id),
            }

        if method == "messages.list":
            q = (params.get("q") or "").strip()
            max_res = int(params.get("maxResults") or 100)
            page_tok = params.get("pageToken")
            matched = self._filter_messages(q)
            start = int(page_tok) if page_tok else 0
            sliced = matched[start : start + max_res]
            next_tok = str(start + max_res) if (start + max_res) < len(matched) else None
            out: dict[str, Any] = {
                "messages": [{"id": m["id"], "threadId": m["threadId"]} for m in sliced],
                "resultSizeEstimate": len(matched),
            }
            if next_tok:
                out["nextPageToken"] = next_tok
            return out

        if method == "messages.get":
            mid = params["id"]
            fmt = (params.get("format") or "full").lower()
            if mid not in self.messages:
                raise make_http_error(404, "notFound", f"Message {mid} not found")
            msg = self.messages[mid]
            if fmt == "minimal":
                return {
                    "id": msg["id"],
                    "threadId": msg["threadId"],
                    "labelIds": list(msg["labelIds"]),
                    "snippet": msg["snippet"],
                    "historyId": msg["historyId"],
                }
            if fmt == "metadata":
                return {
                    "id": msg["id"],
                    "threadId": msg["threadId"],
                    "labelIds": list(msg["labelIds"]),
                    "snippet": msg["snippet"],
                    "historyId": msg["historyId"],
                    "payload": {"headers": list(msg["payload"]["headers"])},
                }
            if fmt == "raw":
                return {
                    "id": msg["id"],
                    "threadId": msg["threadId"],
                    "labelIds": list(msg["labelIds"]),
                    "snippet": msg["snippet"],
                    "raw": msg["raw"],
                }
            return dict(msg)

        if method == "messages.send":
            body = params["body"]
            raw_b64 = body["raw"]
            raw_bytes = base64.urlsafe_b64decode(raw_b64 + "===")
            em = BytesParser(policy=policy.default).parsebytes(raw_bytes)
            mid = f"sent_{self._msg_seq}"
            self._msg_seq += 1
            tid = body.get("threadId") or f"thread_{mid}"
            rfc_id = str(em.get("Message-ID") or f"<{mid}@gmail-mcp.local>")
            subj = str(em.get("Subject") or "")
            to_hdr = str(em.get("To") or "")
            from_hdr = str(em.get("From") or self.email_address)
            in_rep = str(em.get("In-Reply-To") or "")
            refs = str(em.get("References") or "")

            headers_list = [
                {"name": "From", "value": from_hdr},
                {"name": "To", "value": to_hdr},
                {"name": "Subject", "value": subj},
                {"name": "Message-ID", "value": rfc_id},
            ]
            if in_rep:
                headers_list.append({"name": "In-Reply-To", "value": in_rep})
            if refs:
                headers_list.append({"name": "References", "value": refs})

            record = {
                "id": mid,
                "threadId": tid,
                "labelIds": ["SENT"],
                "snippet": subj,
                "historyId": str(self.history_id),
                "raw": raw_b64,
                "payload": {"headers": headers_list, "parts": []},
            }
            self.messages[mid] = record
            self.threads.setdefault(tid, []).append(mid)
            return {"id": mid, "threadId": tid, "labelIds": ["SENT"]}

        if method == "messages.modify":
            mid = params["id"]
            if mid not in self.messages:
                raise make_http_error(404, "notFound", f"Message {mid} not found")
            body = params["body"]
            lbls = set(self.messages[mid]["labelIds"])
            for a in body.get("addLabelIds") or []:
                lbls.add(a)
            for r in body.get("removeLabelIds") or []:
                lbls.discard(r)
            self.messages[mid]["labelIds"] = list(lbls)
            return self.messages[mid]

        if method == "messages.batchModify":
            body = params["body"]
            ids = body.get("ids") or []
            for mid in ids:
                if mid in self.messages:
                    lbls = set(self.messages[mid]["labelIds"])
                    for a in body.get("addLabelIds") or []:
                        lbls.add(a)
                    for r in body.get("removeLabelIds") or []:
                        lbls.discard(r)
                    self.messages[mid]["labelIds"] = list(lbls)
            return {}

        if method == "messages.trash":
            mid = params["id"]
            if mid not in self.messages:
                raise make_http_error(404, "notFound", f"Message {mid} not found")
            lbls = set(self.messages[mid]["labelIds"])
            lbls.discard("INBOX")
            lbls.add("TRASH")
            self.messages[mid]["labelIds"] = list(lbls)
            return self.messages[mid]

        if method == "messages.untrash":
            mid = params["id"]
            if mid not in self.messages:
                raise make_http_error(404, "notFound", f"Message {mid} not found")
            lbls = set(self.messages[mid]["labelIds"])
            lbls.discard("TRASH")
            lbls.add("INBOX")
            self.messages[mid]["labelIds"] = list(lbls)
            return self.messages[mid]

        if method == "messages.delete":
            mid = params["id"]
            self.messages.pop(mid, None)
            return {}

        if method == "messages.attachments.get":
            mid = params["messageId"]
            att_id = params["id"]
            data = self.attachments.get((mid, att_id))
            if data is None:
                raise make_http_error(404, "notFound", f"Attachment {att_id} not found")
            return {
                "attachmentId": att_id,
                "size": len(data),
                "data": base64.urlsafe_b64encode(data).decode("ascii"),
            }

        if method == "threads.list":
            q = (params.get("q") or "").strip()
            max_res = int(params.get("maxResults") or 100)
            page_tok = params.get("pageToken")
            matched_msgs = self._filter_messages(q)
            seen_tids: list[str] = []
            for m in matched_msgs:
                tid = m["threadId"]
                if tid not in seen_tids:
                    seen_tids.append(tid)
            start = int(page_tok) if page_tok else 0
            sliced = seen_tids[start : start + max_res]
            next_tok = str(start + max_res) if (start + max_res) < len(seen_tids) else None
            out = {
                "threads": [{"id": tid, "snippet": "Thread snippet", "historyId": str(self.history_id)} for tid in sliced],
                "resultSizeEstimate": len(seen_tids),
            }
            if next_tok:
                out["nextPageToken"] = next_tok
            return out

        if method == "threads.get":
            tid = params["id"]
            if tid not in self.threads:
                raise make_http_error(404, "notFound", f"Thread {tid} not found")
            mids = [m for m in self.threads[tid] if m in self.messages]
            if not mids:
                raise make_http_error(404, "notFound", f"Thread {tid} has no messages")
            return {
                "id": tid,
                "historyId": str(self.history_id),
                "messages": [self.messages[m] for m in mids],
            }

        if method == "threads.modify":
            tid = params["id"]
            if tid not in self.threads:
                raise make_http_error(404, "notFound", f"Thread {tid} not found")
            body = params["body"]
            for mid in self.threads[tid]:
                if mid in self.messages:
                    lbls = set(self.messages[mid]["labelIds"])
                    for a in body.get("addLabelIds") or []:
                        lbls.add(a)
                    for r in body.get("removeLabelIds") or []:
                        lbls.discard(r)
                    self.messages[mid]["labelIds"] = list(lbls)
            return {"id": tid}

        if method == "drafts.create":
            msg_body = params["body"].get("message") or {}
            raw_b64 = msg_body.get("raw") or ""
            did = f"draft_{self._draft_seq}"
            self._draft_seq += 1
            mid = f"dmsg_{did}"
            tid = msg_body.get("threadId") or f"dthread_{did}"
            draft_rec = {
                "id": did,
                "message": {
                    "id": mid,
                    "threadId": tid,
                    "labelIds": ["DRAFT"],
                    "raw": raw_b64,
                    "payload": {"headers": [], "parts": []},
                },
            }
            self.drafts[did] = draft_rec
            return draft_rec

        if method == "drafts.get":
            did = params["id"]
            if did not in self.drafts:
                raise make_http_error(404, "notFound", f"Draft {did} not found")
            return self.drafts[did]

        if method == "drafts.list":
            max_res = int(params.get("maxResults") or 100)
            page_tok = params.get("pageToken")
            all_drafts = list(self.drafts.values())
            start = int(page_tok) if page_tok else 0
            sliced = all_drafts[start : start + max_res]
            next_tok = str(start + max_res) if (start + max_res) < len(all_drafts) else None
            out = {
                "drafts": [{"id": d["id"], "message": {"id": d["message"]["id"], "threadId": d["message"]["threadId"]}} for d in sliced],
                "resultSizeEstimate": len(all_drafts),
            }
            if next_tok:
                out["nextPageToken"] = next_tok
            return out

        if method == "drafts.update":
            did = params["id"]
            if did not in self.drafts:
                raise make_http_error(404, "notFound", f"Draft {did} not found")
            msg_body = params["body"].get("message") or {}
            self.drafts[did]["message"]["raw"] = msg_body.get("raw") or self.drafts[did]["message"]["raw"]
            if msg_body.get("threadId"):
                self.drafts[did]["message"]["threadId"] = msg_body["threadId"]
            return self.drafts[did]

        if method == "drafts.delete":
            did = params["id"]
            if did not in self.drafts:
                raise make_http_error(404, "notFound", f"Draft {did} not found")
            self.drafts.pop(did, None)
            return {}

        if method == "labels.list":
            return {"labels": list(self.labels.values())}

        if method == "labels.get":
            lid = params["id"]
            if lid not in self.labels:
                raise make_http_error(404, "notFound", f"Label {lid} not found")
            lbl = dict(self.labels[lid])
            matching = [m for m in self.messages.values() if lid in m.get("labelIds", [])]
            unread = [m for m in matching if "UNREAD" in m.get("labelIds", [])]
            lbl["messagesTotal"] = len(matching)
            lbl["messagesUnread"] = len(unread)
            lbl["threadsTotal"] = len({m["threadId"] for m in matching})
            lbl["threadsUnread"] = len({m["threadId"] for m in unread})
            return lbl

        if method == "labels.create":
            body = params["body"]
            lid = f"Label_{self._label_seq}"
            self._label_seq += 1
            rec = {
                "id": lid,
                "name": body["name"],
                "type": "user",
                "labelListVisibility": body.get("labelListVisibility", "labelShow"),
                "messageListVisibility": body.get("messageListVisibility", "show"),
                "messagesTotal": 0,
                "messagesUnread": 0,
                "threadsTotal": 0,
                "threadsUnread": 0,
            }
            self.labels[lid] = rec
            return rec

        if method == "labels.update":
            lid = params["id"]
            if lid not in self.labels:
                raise make_http_error(404, "notFound", f"Label {lid} not found")
            body = params["body"]
            self.labels[lid].update(body)
            return self.labels[lid]

        if method == "labels.delete":
            lid = params["id"]
            if lid not in self.labels:
                raise make_http_error(404, "notFound", f"Label {lid} not found")
            self.labels.pop(lid, None)
            return {}

        if method == "settings.filters.list":
            return {"filter": list(self.filters.values())}

        if method == "settings.filters.get":
            fid = params["id"]
            if fid not in self.filters:
                raise make_http_error(404, "notFound", f"Filter {fid} not found")
            return self.filters[fid]

        if method == "settings.filters.create":
            body = params["body"]
            fid = f"filter_{self._filter_seq}"
            self._filter_seq += 1
            rec = {"id": fid, "criteria": body.get("criteria", {}), "action": body.get("action", {})}
            self.filters[fid] = rec
            return rec

        if method == "settings.filters.delete":
            fid = params["id"]
            if fid not in self.filters:
                raise make_http_error(404, "notFound", f"Filter {fid} not found")
            self.filters.pop(fid, None)
            return {}

        if method == "settings.getVacation":
            return dict(self.vacation_settings)

        if method == "settings.updateVacation":
            self.vacation_settings.update(params["body"])
            return dict(self.vacation_settings)

        if method == "settings.getAutoForwarding":
            return {"enabled": False}

        if method == "settings.sendAs.list":
            return {"sendAs": list(self.send_as_aliases)}

        if method == "watch":
            self.history_id += 1
            return {
                "historyId": str(self.history_id),
                "expiration": str(int((datetime.now(timezone.utc) + timedelta(days=7)).timestamp() * 1000)),
            }

        if method == "stop":
            return {}

        if method == "history.list":
            start_hid = str(params.get("startHistoryId") or "")
            if start_hid in self.expired_history_ids:
                raise make_http_error(404, "notFound", f"startHistoryId {start_hid} is expired/too old")
            return {
                "history": list(self.history_records),
                "historyId": str(self.history_id),
            }

        raise ValueError(f"Unhandled mock method: {method}")

    def _filter_messages(self, query: str) -> list[dict[str, Any]]:
        if not query:
            return list(self.messages.values())
        q_lower = query.lower()
        results: list[dict[str, Any]] = []
        for msg in self.messages.values():
            hdrs = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
            lbls = [l.lower() for l in msg.get("labelIds", [])]

            if "is:unread" in q_lower and "unread" not in lbls:
                continue
            if "in:sent" in q_lower and "sent" not in lbls:
                continue
            if "in:inbox" in q_lower and "inbox" not in lbls:
                continue
            if "has:attachment" in q_lower:
                parts = msg.get("payload", {}).get("parts", [])
                if not any(p.get("filename") for p in parts):
                    continue
            if "from:" in q_lower:
                for token in q_lower.split():
                    if token.startswith("from:"):
                        expected_from = token.split("from:", 1)[1].strip('"')
                        if expected_from not in hdrs.get("from", "").lower():
                            break
                else:
                    pass
                if any(
                    tok.startswith("from:") and tok.split("from:", 1)[1].strip('"') not in hdrs.get("from", "").lower()
                    for tok in q_lower.split()
                ):
                    continue
            if "rfc822msgid:" in q_lower:
                for token in q_lower.split():
                    if token.startswith("rfc822msgid:"):
                        target_rfc = token.split("rfc822msgid:", 1)[1].strip("<>\"'")
                        actual_rfc = hdrs.get("message-id", "").strip("<>\"'").lower()
                        if target_rfc != actual_rfc:
                            break
                else:
                    results.append(msg)
                    continue
                continue
            if "subject:" in q_lower:
                subj_val = hdrs.get("subject", "").lower()
                if 'subject:"' in q_lower:
                    expected_subj = q_lower.split('subject:"', 1)[1].split('"', 1)[0]
                else:
                    expected_subj = q_lower.split("subject:", 1)[1].split()[0]
                if expected_subj not in subj_val:
                    continue
            results.append(msg)
        return results


@pytest.fixture()
def temp_workspace(tmp_path: Path) -> dict[str, Path]:
    """Create isolated allowed directories and sample attachment files in a temporary directory."""
    allowed_att = tmp_path / "attachments"
    allowed_dl = tmp_path / "downloads"
    allowed_exp = tmp_path / "exports"
    allowed_res = tmp_path / "Resumes"
    for d in (allowed_att, allowed_dl, allowed_exp, allowed_res):
        d.mkdir(parents=True, exist_ok=True)

    sample_pdf = allowed_att / "resume.pdf"
    sample_pdf.write_bytes(b"%PDF-1.4\n%Sample Resume PDF Content\n%%EOF")

    sample_pdf2 = allowed_att / "portfolio.pdf"
    sample_pdf2.write_bytes(b"%PDF-1.4\n%Sample Portfolio PDF Content\n%%EOF")

    return {
        "root": tmp_path,
        "attachments": allowed_att,
        "downloads": allowed_dl,
        "exports": allowed_exp,
        "resumes": allowed_res,
        "resume_pdf": sample_pdf,
        "portfolio_pdf": sample_pdf2,
    }


@pytest.fixture()
def mock_backend() -> MockGmailBackend:
    return MockGmailBackend()


@pytest.fixture()
def gmail_client(temp_workspace: dict[str, Path], mock_backend: MockGmailBackend) -> GmailClientWrapper:
    """Create a GmailClientWrapper wired to an in-memory SQLite DB, valid mock credentials, and MockGmailBackend."""
    root = temp_workspace["root"]
    config = GmailConfig(
        workspace_root=root,
        credentials_path=root / "credentials.json",
        token_path=root / "token.json",
        db_path=root / "test_gmail_mcp.db",
        allowed_dirs=[
            temp_workspace["attachments"],
            temp_workspace["downloads"],
            temp_workspace["exports"],
            temp_workspace["resumes"],
        ],
        # Use zero send interval in unit/stress tests so 100-send tests complete in milliseconds
        send_interval_seconds=0.0,
        # Allow high per-minute units for 1,000-message stress tests while keeping quota tracking active
        user_quota_per_minute=200_000,
        project_quota_per_minute=1_200_000,
    )
    db = DatabaseManager(":memory:")
    token_mgr = TokenManager(config)
    creds = Credentials(
        token="mock_access_token",
        refresh_token="mock_refresh_token",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="mock_client_id",
        client_secret="mock_client_secret",
        scopes=list(DEFAULT_SCOPES),
        expiry=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
    )
    token_mgr.set_credentials(creds, persist=True)
    return GmailClientWrapper(
        config=config,
        db=db,
        token_manager=token_mgr,
        custom_dispatcher=mock_backend.dispatch,
    )
