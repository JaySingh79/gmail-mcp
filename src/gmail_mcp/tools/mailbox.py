"""Mailbox organization (`gmail_modify_messages`) and mailbox settings tools."""

from __future__ import annotations

from typing import Any, Literal

from gmail_mcp.core.gmail_client import GmailClientWrapper
from gmail_mcp.core.safety import SafetyError

MailboxAction = Literal[
    "mark_read",
    "mark_unread",
    "archive",
    "unarchive",
    "star",
    "unstar",
    "mark_important",
    "mark_not_important",
    "move_to_trash",
    "restore_from_trash",
    "mark_spam",
    "remove_from_spam",
    "permanent_delete",
]

ACTION_LABEL_DELTAS: dict[str, tuple[list[str], list[str]]] = {
    "mark_read": ([], ["UNREAD"]),
    "mark_unread": (["UNREAD"], []),
    "archive": ([], ["INBOX"]),
    "unarchive": (["INBOX"], []),
    "star": (["STARRED"], []),
    "unstar": ([], ["STARRED"]),
    "mark_important": (["IMPORTANT"], []),
    "mark_not_important": ([], ["IMPORTANT"]),
    "mark_spam": (["SPAM"], ["INBOX"]),
    "remove_from_spam": (["INBOX"], ["SPAM"]),
}


async def gmail_modify_messages(
    client: GmailClientWrapper,
    action: MailboxAction,
    *,
    message_ids: list[str] | str | None = None,
    thread_ids: list[str] | str | None = None,
    dry_run: bool = False,
    confirm_permanent_delete: str | None = None,
) -> dict[str, Any]:
    """
    Flexible mailbox organization tool supporting:
    `mark_read`, `mark_unread`, `archive`, `unarchive`, `star`, `unstar`,
    `mark_important`, `mark_not_important`, `move_to_trash`, `restore_from_trash`,
    `mark_spam`, `remove_from_spam`, and guarded `permanent_delete`.

    Default destructive behaviour:
    - Uses `move_to_trash` instead of permanent deletion.
    - Never deletes messages based on an ambiguous search query (explicit IDs required).
    - Requires `confirm_permanent_delete="CONFIRM PERMANENT DELETE <N> ITEMS"` for permanent deletion.
    """
    try:
        msg_list = [message_ids] if isinstance(message_ids, str) else list(message_ids or [])
        th_list = [thread_ids] if isinstance(thread_ids, str) else list(thread_ids or [])
        msg_list = [m.strip() for m in msg_list if m and m.strip()]
        th_list = [t.strip() for t in th_list if t and t.strip()]

        total_targets = len(msg_list) + len(th_list)
        if total_targets == 0:
            raise SafetyError(
                "MISSING_TARGET_IDS",
                "Explicit `message_ids` and/or `thread_ids` are required. Ambiguous query-only modification is forbidden.",
            )

        if dry_run:
            return {
                "success": True,
                "operation": "gmail_modify_messages",
                "mutates_mailbox": False,
                "dry_run": True,
                "action": action,
                "affected_messages_count": len(msg_list),
                "affected_threads_count": len(th_list),
                "total_affected": total_targets,
                "quota_estimate": {"units_per_request": 5, "total_units": 0},
            }

        total_units = 0

        if action == "permanent_delete":
            expected_phrase = f"CONFIRM PERMANENT DELETE {total_targets} ITEMS"
            if (confirm_permanent_delete or "").strip() != expected_phrase:
                raise SafetyError(
                    "PERMANENT_DELETE_CONFIRMATION_REQUIRED",
                    (
                        f"Permanent deletion is irreversible. Prefer `action='move_to_trash'`. "
                        f"To permanently delete, pass `confirm_permanent_delete='{expected_phrase}'`."
                    ),
                    {"expected_confirmation": expected_phrase, "recommended_alternative": "move_to_trash"},
                )
            for mid in msg_list:
                _, meta = await client.execute_api(
                    "messages.delete",
                    {"id": mid},
                    tool_name="gmail_modify_messages",
                    op_class="write",
                )
                total_units += int(meta.get("quota_estimate", 10))
            for tid in th_list:
                _, meta = await client.execute_api(
                    "threads.delete",
                    {"id": tid},
                    tool_name="gmail_modify_messages",
                    op_class="write",
                )
                total_units += int(meta.get("quota_estimate", 20))

        elif action in ("move_to_trash", "restore_from_trash"):
            msg_method = "messages.trash" if action == "move_to_trash" else "messages.untrash"
            th_method = "threads.trash" if action == "move_to_trash" else "threads.untrash"
            for mid in msg_list:
                _, meta = await client.execute_api(
                    msg_method,
                    {"id": mid},
                    tool_name="gmail_modify_messages",
                    op_class="write",
                )
                total_units += int(meta.get("quota_estimate", 5))
            for tid in th_list:
                _, meta = await client.execute_api(
                    th_method,
                    {"id": tid},
                    tool_name="gmail_modify_messages",
                    op_class="write",
                )
                total_units += int(meta.get("quota_estimate", 10))

        elif action in ACTION_LABEL_DELTAS:
            add_lbls, rem_lbls = ACTION_LABEL_DELTAS[action]
            body_delta: dict[str, Any] = {}
            if add_lbls:
                body_delta["addLabelIds"] = add_lbls
            if rem_lbls:
                body_delta["removeLabelIds"] = rem_lbls

            if len(msg_list) == 1:
                _, meta = await client.execute_api(
                    "messages.modify",
                    {"id": msg_list[0], "body": body_delta},
                    tool_name="gmail_modify_messages",
                    op_class="write",
                )
                total_units += int(meta.get("quota_estimate", 5))
            elif len(msg_list) > 1:
                for start in range(0, len(msg_list), 1000):
                    chunk = msg_list[start : start + 1000]
                    batch_body = {"ids": chunk, **body_delta}
                    _, meta = await client.execute_api(
                        "messages.batchModify",
                        {"body": batch_body},
                        tool_name="gmail_modify_messages",
                        op_class="write",
                    )
                    total_units += int(meta.get("quota_estimate", 50))

            for tid in th_list:
                _, meta = await client.execute_api(
                    "threads.modify",
                    {"id": tid, "body": body_delta},
                    tool_name="gmail_modify_messages",
                    op_class="write",
                )
                total_units += int(meta.get("quota_estimate", 10))
        else:
            raise SafetyError("UNSUPPORTED_ACTION", f"Unsupported mailbox action: '{action}'.")

        return {
            "success": True,
            "operation": "gmail_modify_messages",
            "mutates_mailbox": True,
            "action": action,
            "messages_modified": len(msg_list),
            "threads_modified": len(th_list),
            "total_modified": total_targets,
            "quota_estimate": {
                "units_per_request": 50 if len(msg_list) > 1 else 5,
                "total_units": total_units,
            },
        }
    except Exception as exc:
        return client.format_tool_error("gmail_modify_messages", exc, mutates_mailbox=not dry_run)


async def gmail_get_mailbox_settings(client: GmailClientWrapper) -> dict[str, Any]:
    """Read vacation-responder settings, verified send-as aliases, and auto-forwarding settings."""
    try:
        total_units = 0
        vac_resp, v_meta = await client.execute_api(
            "settings.getVacation", {}, tool_name="gmail_get_mailbox_settings", op_class="read"
        )
        total_units += int(v_meta.get("quota_estimate", 1))

        aliases_resp, a_meta = await client.execute_api(
            "settings.sendAs.list", {}, tool_name="gmail_get_mailbox_settings", op_class="read"
        )
        total_units += int(a_meta.get("quota_estimate", 1))

        fwd_resp: dict[str, Any] = {}
        try:
            fwd_resp, f_meta = await client.execute_api(
                "settings.getAutoForwarding", {}, tool_name="gmail_get_mailbox_settings", op_class="read"
            )
            total_units += int(f_meta.get("quota_estimate", 1))
        except Exception:
            fwd_resp = {"enabled": False}

        send_as_list = [
            {
                "sendAsEmail": item.get("sendAsEmail"),
                "displayName": item.get("displayName"),
                "replyToAddress": item.get("replyToAddress"),
                "isPrimary": bool(item.get("isPrimary")),
                "isDefault": bool(item.get("isDefault")),
                "verificationStatus": item.get("verificationStatus", "accepted"),
            }
            for item in (aliases_resp.get("sendAs") or [])
        ]

        return {
            "success": True,
            "operation": "gmail_get_mailbox_settings",
            "mutates_mailbox": False,
            "vacation_settings": vac_resp,
            "send_as_aliases": send_as_list,
            "auto_forwarding": fwd_resp,
            "quota_estimate": {
                "units_per_request": 1,
                "total_units": total_units,
            },
        }
    except Exception as exc:
        return client.format_tool_error("gmail_get_mailbox_settings", exc, mutates_mailbox=False)


async def gmail_update_vacation_settings(
    client: GmailClientWrapper,
    enable_auto_reply: bool,
    *,
    response_subject: str | None = None,
    response_body_plain_text: str | None = None,
    response_body_html: str | None = None,
    restrict_to_contacts: bool = False,
    restrict_to_domain: bool = False,
    start_time_epoch_ms: int | None = None,
    end_time_epoch_ms: int | None = None,
    confirm_update: bool = False,
) -> dict[str, Any]:
    """Update Gmail vacation responder settings (requires explicit `confirm_update=True` and `gmail.settings.basic` scope)."""
    try:
        body: dict[str, Any] = {
            "enableAutoReply": bool(enable_auto_reply),
            "restrictToContacts": bool(restrict_to_contacts),
            "restrictToDomain": bool(restrict_to_domain),
        }
        if response_subject is not None:
            body["responseSubject"] = response_subject
        if response_body_plain_text is not None:
            body["responseBodyPlainText"] = response_body_plain_text
        if response_body_html is not None:
            body["responseBodyHtml"] = response_body_html
        if start_time_epoch_ms is not None:
            body["startTime"] = str(start_time_epoch_ms)
        if end_time_epoch_ms is not None:
            body["endTime"] = str(end_time_epoch_ms)

        if not confirm_update:
            return {
                "success": True,
                "operation": "gmail_update_vacation_settings",
                "mutates_mailbox": False,
                "status": "preview_requires_confirmation",
                "requires_confirmation": True,
                "proposed_vacation_settings": body,
                "quota_estimate": {"units_per_request": 5, "total_units": 0},
            }

        resp, meta = await client.execute_api(
            "settings.updateVacation",
            {"body": body},
            tool_name="gmail_update_vacation_settings",
            op_class="write",
        )
        return {
            "success": True,
            "operation": "gmail_update_vacation_settings",
            "mutates_mailbox": True,
            "vacation_settings": resp,
            "quota_estimate": {
                "units_per_request": 5,
                "total_units": int(meta.get("quota_estimate", 5)),
            },
        }
    except Exception as exc:
        return client.format_tool_error("gmail_update_vacation_settings", exc, mutates_mailbox=confirm_update)
